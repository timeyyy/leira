"""Leira v1.5 Git provenance: observed source state, not source control."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass

from leira.dispatcher.git import inspect_repo
from leira.dispatcher.kernel import LedgerKernel

PROVENANCE_WORKER_ID = "kernel"
PROVENANCE_CAPTURED_EVENT = "provenance_captured"
PROVENANCE_CAPTURE_FAILED_EVENT = "provenance_capture_failed"
PROVENANCE_EVENT_TYPES = frozenset(
    {PROVENANCE_CAPTURED_EVENT, PROVENANCE_CAPTURE_FAILED_EVENT}
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS provenance_projection (
    snapshot_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    head_sha TEXT,
    branch TEXT,
    is_dirty INTEGER,
    status_porcelain TEXT NOT NULL,
    created_at TEXT NOT NULL,
    error_type TEXT,
    stderr TEXT NOT NULL,
    last_event_id TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ProvenanceSnapshot:
    snapshot_id: str
    intent_id: str
    repo_path: str
    head_sha: str | None
    branch: str | None
    is_dirty: bool | None
    status_porcelain: str
    created_at: str
    error_type: str | None = None
    stderr: str = ""


def ensure_schema(ledger: LedgerKernel) -> None:
    ledger.connection.executescript(_SCHEMA)
    ledger.connection.commit()


def _snapshot_payload(snapshot: ProvenanceSnapshot) -> dict:
    content = asdict(snapshot)
    content.pop("created_at")
    return {"type": "provenance", "content": content}


def _row_to_snapshot(row: tuple) -> ProvenanceSnapshot:
    is_dirty = row[5]
    return ProvenanceSnapshot(
        snapshot_id=row[0],
        intent_id=row[1],
        repo_path=row[2],
        head_sha=row[3],
        branch=row[4],
        is_dirty=None if is_dirty is None else bool(is_dirty),
        status_porcelain=row[6],
        created_at=row[7],
        error_type=row[8],
        stderr=row[9],
    )


def _insert_projection(
    ledger: LedgerKernel, snapshot: ProvenanceSnapshot, last_event_id: str
) -> bool:
    try:
        ensure_schema(ledger)
        with ledger.connection:
            ledger.connection.execute(
                """
                INSERT INTO provenance_projection
                    (snapshot_id, intent_id, repo_path, head_sha, branch, is_dirty,
                     status_porcelain, created_at, error_type, stderr, last_event_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    intent_id = excluded.intent_id,
                    repo_path = excluded.repo_path,
                    head_sha = excluded.head_sha,
                    branch = excluded.branch,
                    is_dirty = excluded.is_dirty,
                    status_porcelain = excluded.status_porcelain,
                    created_at = excluded.created_at,
                    error_type = excluded.error_type,
                    stderr = excluded.stderr,
                    last_event_id = excluded.last_event_id
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.intent_id,
                    snapshot.repo_path,
                    snapshot.head_sha,
                    snapshot.branch,
                    None if snapshot.is_dirty is None else int(snapshot.is_dirty),
                    snapshot.status_porcelain,
                    snapshot.created_at,
                    snapshot.error_type,
                    snapshot.stderr,
                    last_event_id,
                ),
            )
        return True
    except sqlite3.Error:
        return False


def capture_provenance(
    ledger: LedgerKernel, intent_id: str, repo_path: str
) -> ProvenanceSnapshot:
    """Capture repo_path's Git state for intent_id and record it in the ledger."""
    snapshot_id = str(uuid.uuid4())
    result = inspect_repo(repo_path)
    event_type = (
        PROVENANCE_CAPTURED_EVENT if result.success else PROVENANCE_CAPTURE_FAILED_EVENT
    )
    draft = ProvenanceSnapshot(
        snapshot_id=snapshot_id,
        intent_id=intent_id,
        repo_path=repo_path,
        head_sha=result.head_sha,
        branch=result.branch,
        is_dirty=result.is_dirty,
        status_porcelain=result.status_porcelain,
        created_at="",
        error_type=result.error_type,
        stderr=result.stderr,
    )
    append_result = ledger.append_event(
        event_type=event_type,
        worker_id=PROVENANCE_WORKER_ID,
        payload=_snapshot_payload(draft),
        operation_id=intent_id,
    )
    if not append_result.success:
        return ProvenanceSnapshot(
            snapshot_id=snapshot_id,
            intent_id=intent_id,
            repo_path=repo_path,
            head_sha=result.head_sha if result.success else None,
            branch=result.branch if result.success else None,
            is_dirty=result.is_dirty if result.success else None,
            status_porcelain=result.status_porcelain if result.success else "",
            created_at="",
            error_type="STORAGE_FAILURE",
            stderr=append_result.message or "",
        )

    snapshot = ProvenanceSnapshot(
        snapshot_id=snapshot_id,
        intent_id=intent_id,
        repo_path=repo_path,
        head_sha=result.head_sha,
        branch=result.branch,
        is_dirty=result.is_dirty,
        status_porcelain=result.status_porcelain,
        created_at=append_result.created_at or "",
        error_type=result.error_type,
        stderr=result.stderr,
    )
    _insert_projection(ledger, snapshot, append_result.event_id or "")
    return snapshot


class GitProvenance:
    """Small library facade over one ledger's provenance projection."""

    def __init__(self, ledger: LedgerKernel):
        self._ledger = ledger
        ensure_schema(self._ledger)

    def capture_provenance(self, intent_id: str, repo_path: str) -> ProvenanceSnapshot:
        return capture_provenance(self._ledger, intent_id, repo_path)

    def get_provenance(self, snapshot_id: str) -> ProvenanceSnapshot | None:
        return get_provenance(self._ledger, snapshot_id)


def get_provenance(
    ledger: LedgerKernel, snapshot_id: str
) -> ProvenanceSnapshot | None:
    ensure_schema(ledger)
    row = ledger.connection.execute(
        """
        SELECT snapshot_id, intent_id, repo_path, head_sha, branch, is_dirty,
               status_porcelain, created_at, error_type, stderr, last_event_id
        FROM provenance_projection
        WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    return _row_to_snapshot(row) if row else None


def parse_provenance_event(event: dict) -> tuple[ProvenanceSnapshot, str] | None:
    if event["event_type"] not in PROVENANCE_EVENT_TYPES:
        return None
    try:
        payload = json.loads(event["payload_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != "provenance":
        return None
    content = payload.get("content")
    if not isinstance(content, dict):
        return None
    try:
        snapshot = ProvenanceSnapshot(
            snapshot_id=content["snapshot_id"],
            intent_id=content["intent_id"],
            repo_path=content["repo_path"],
            head_sha=content.get("head_sha"),
            branch=content.get("branch"),
            is_dirty=content.get("is_dirty"),
            status_porcelain=content["status_porcelain"],
            created_at=event["created_at"],
            error_type=content.get("error_type"),
            stderr=content.get("stderr", ""),
        )
    except KeyError:
        return None
    return snapshot, event["id"]


def rebuild_provenance_projection(ledger: LedgerKernel) -> None:
    ensure_schema(ledger)
    rows = ledger.connection.execute(
        """
        SELECT id, event_type, payload_json, created_at
        FROM ledger_events
        WHERE event_type IN (?, ?)
        ORDER BY rowid
        """,
        (PROVENANCE_CAPTURED_EVENT, PROVENANCE_CAPTURE_FAILED_EVENT),
    ).fetchall()
    events = [
        {"id": row[0], "event_type": row[1], "payload_json": row[2], "created_at": row[3]}
        for row in rows
    ]

    with ledger.connection:
        ledger.connection.execute("DELETE FROM provenance_projection")
        for event in events:
            parsed = parse_provenance_event(event)
            if parsed is None:
                continue
            snapshot, last_event_id = parsed
            ledger.connection.execute(
                """
                INSERT INTO provenance_projection
                    (snapshot_id, intent_id, repo_path, head_sha, branch, is_dirty,
                     status_porcelain, created_at, error_type, stderr, last_event_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.intent_id,
                    snapshot.repo_path,
                    snapshot.head_sha,
                    snapshot.branch,
                    None if snapshot.is_dirty is None else int(snapshot.is_dirty),
                    snapshot.status_porcelain,
                    snapshot.created_at,
                    snapshot.error_type,
                    snapshot.stderr,
                    last_event_id,
                ),
            )
