"""Leira v1.6 sessions: evidence correlation, not orchestration."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from leira.dispatcher.kernel import LedgerKernel
from leira.environment.environment import EnvironmentSnapshot, get_environment
from leira.inbox.inbox import get_intent_status
from leira.provenance.git_provenance import ProvenanceSnapshot
from leira.receipts.receipts import ReceiptBundle, get_receipt_bundle
from leira.workspace.workspace import ArtifactDescriptor, descriptor_from_projection_row

SESSIONS_WORKER_ID = "kernel"
SESSION_CREATED_EVENT = "session_created"
SESSION_INTENT_ADDED_EVENT = "session_intent_added"
SESSION_INTENT_REJECTED_EVENT = "session_intent_rejected"
SESSION_EVENT_TYPES = frozenset(
    {SESSION_CREATED_EVENT, SESSION_INTENT_ADDED_EVENT, SESSION_INTENT_REJECTED_EVENT}
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_projection (
    session_id TEXT PRIMARY KEY,
    intent_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_membership_projection (
    session_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    membership_order INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (session_id, intent_id)
);
"""


@dataclass(frozen=True)
class SessionBundle:
    session_id: str
    created_at: str
    intent_ids: list[str]


@dataclass(frozen=True)
class SessionResult:
    success: bool
    session_id: str | None = None
    intent_id: str | None = None
    error_type: str | None = None


def ensure_schema(ledger: LedgerKernel) -> None:
    ledger.connection.executescript(_SCHEMA)
    ledger.connection.commit()


def _session_artifact(session_id: str, intent_id: str | None, error_type: str | None) -> dict:
    return {
        "type": "session_membership",
        "content": {
            "session_id": session_id,
            "intent_id": intent_id,
            "error_type": error_type,
        },
    }


def _payload_session_id(payload: dict) -> str | None:
    artifact = payload.get("artifact")
    content = artifact.get("content") if isinstance(artifact, dict) else None
    session_id = content.get("session_id") if isinstance(content, dict) else None
    if isinstance(session_id, str) and session_id:
        return session_id
    session_id = payload.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def _payload_intent_id(payload: dict) -> str | None:
    artifact = payload.get("artifact")
    content = artifact.get("content") if isinstance(artifact, dict) else None
    intent_id = content.get("intent_id") if isinstance(content, dict) else None
    if isinstance(intent_id, str) and intent_id:
        return intent_id
    intent_id = payload.get("intent_id")
    return intent_id if isinstance(intent_id, str) and intent_id else None


def _session_exists(ledger: LedgerKernel, session_id: str) -> bool:
    row = ledger.connection.execute(
        "SELECT 1 FROM ledger_events WHERE event_type = ? AND payload_json LIKE ? LIMIT 1",
        (SESSION_CREATED_EVENT, f'%"session_id":"{session_id}"%'),
    ).fetchone()
    return row is not None


def _membership_exists(ledger: LedgerKernel, session_id: str, intent_id: str) -> bool:
    row = ledger.connection.execute(
        """
        SELECT 1 FROM ledger_events
        WHERE event_type = ?
        AND payload_json LIKE ?
        AND payload_json LIKE ?
        LIMIT 1
        """,
        (
            SESSION_INTENT_ADDED_EVENT,
            f'%"session_id":"{session_id}"%',
            f'%"intent_id":"{intent_id}"%',
        ),
    ).fetchone()
    return row is not None


def _append_rejection(
    ledger: LedgerKernel, session_id: str, intent_id: str, error_type: str
) -> None:
    ledger.append_event(
        event_type=SESSION_INTENT_REJECTED_EVENT,
        worker_id=SESSIONS_WORKER_ID,
        payload={
            "session_id": session_id,
            "intent_id": intent_id,
            "error_type": error_type,
            "artifact": _session_artifact(session_id, intent_id, error_type),
        },
    )


def create_session(ledger: LedgerKernel) -> SessionBundle:
    ensure_schema(ledger)
    session_id = str(uuid.uuid4())
    append_result = ledger.append_event(
        event_type=SESSION_CREATED_EVENT,
        worker_id=SESSIONS_WORKER_ID,
        payload={
            "session_id": session_id,
            "artifact": _session_artifact(session_id, None, None),
        },
    )
    if not append_result.success:
        raise RuntimeError(append_result.error_type or "STORAGE_FAILURE")
    created_at = append_result.created_at or ""
    with ledger.connection:
        ledger.connection.execute(
            """
            INSERT INTO session_projection
                (session_id, intent_count, created_at, last_event_id)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, 0, created_at, append_result.event_id),
        )
    return SessionBundle(session_id=session_id, created_at=created_at, intent_ids=[])


def add_intent_to_session(
    ledger: LedgerKernel, session_id: str, intent_id: str
) -> SessionResult:
    ensure_schema(ledger)
    if not _session_exists(ledger, session_id):
        _append_rejection(ledger, session_id, intent_id, "UNKNOWN_SESSION")
        return SessionResult(False, session_id=session_id, intent_id=intent_id, error_type="UNKNOWN_SESSION")
    if get_intent_status(ledger, intent_id) is None:
        _append_rejection(ledger, session_id, intent_id, "UNKNOWN_INTENT")
        return SessionResult(False, session_id=session_id, intent_id=intent_id, error_type="UNKNOWN_INTENT")
    if _membership_exists(ledger, session_id, intent_id):
        _append_rejection(ledger, session_id, intent_id, "DUPLICATE_MEMBERSHIP")
        return SessionResult(
            False,
            session_id=session_id,
            intent_id=intent_id,
            error_type="DUPLICATE_MEMBERSHIP",
        )

    append_result = ledger.append_event(
        event_type=SESSION_INTENT_ADDED_EVENT,
        worker_id=SESSIONS_WORKER_ID,
        payload={
            "session_id": session_id,
            "intent_id": intent_id,
            "error_type": None,
            "artifact": _session_artifact(session_id, intent_id, None),
        },
    )
    if not append_result.success:
        return SessionResult(False, session_id=session_id, intent_id=intent_id, error_type="STORAGE_FAILURE")

    order = len(list_session_intents(ledger, session_id)) + 1
    with ledger.connection:
        ledger.connection.execute(
            """
            INSERT INTO session_membership_projection
                (session_id, intent_id, membership_order, created_at, last_event_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, intent_id, order, append_result.created_at, append_result.event_id),
        )
        ledger.connection.execute(
            """
            UPDATE session_projection
            SET intent_count = ?, last_event_id = ?
            WHERE session_id = ?
            """,
            (order, append_result.event_id, session_id),
        )
    return SessionResult(True, session_id=session_id, intent_id=intent_id)


def list_session_intents(ledger: LedgerKernel, session_id: str) -> list[str]:
    ensure_schema(ledger)
    rows = ledger.connection.execute(
        """
        SELECT intent_id
        FROM session_membership_projection
        WHERE session_id = ?
        ORDER BY membership_order
        """,
        (session_id,),
    ).fetchall()
    return [row[0] for row in rows]


def get_session(ledger: LedgerKernel, session_id: str) -> SessionBundle | None:
    ensure_schema(ledger)
    row = ledger.connection.execute(
        "SELECT session_id, created_at FROM session_projection WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return SessionBundle(
        session_id=row[0],
        created_at=row[1],
        intent_ids=list_session_intents(ledger, session_id),
    )


def get_session_receipts(ledger: LedgerKernel, session_id: str) -> list[ReceiptBundle]:
    return [
        bundle
        for intent_id in list_session_intents(ledger, session_id)
        for bundle in (get_receipt_bundle(ledger, intent_id),)
        if bundle is not None
    ]


def get_session_artifacts(ledger: LedgerKernel, session_id: str) -> list[ArtifactDescriptor]:
    ensure_schema(ledger)
    descriptors: list[ArtifactDescriptor] = []
    for intent_id in list_session_intents(ledger, session_id):
        rows = ledger.connection.execute(
            """
            SELECT artifact_id, intent_id, relative_path, sha256, size_bytes, created_at
            FROM artifact_projection
            WHERE intent_id = ?
            ORDER BY created_at, artifact_id
            """,
            (intent_id,),
        ).fetchall()
        descriptors.extend(descriptor_from_projection_row(row) for row in rows)
    return descriptors


def _provenance_from_row(row: tuple) -> ProvenanceSnapshot:
    return ProvenanceSnapshot(
        snapshot_id=row[0],
        intent_id=row[1],
        repo_path=row[2],
        head_sha=row[3],
        branch=row[4],
        is_dirty=None if row[5] is None else bool(row[5]),
        status_porcelain=row[6],
        created_at=row[7],
        error_type=row[8],
        stderr=row[9],
    )


def get_session_provenance(ledger: LedgerKernel, session_id: str) -> list[ProvenanceSnapshot]:
    ensure_schema(ledger)
    snapshots: list[ProvenanceSnapshot] = []
    for intent_id in list_session_intents(ledger, session_id):
        rows = ledger.connection.execute(
            """
            SELECT snapshot_id, intent_id, repo_path, head_sha, branch, is_dirty,
                   status_porcelain, created_at, error_type, stderr
            FROM provenance_projection
            WHERE intent_id = ?
            ORDER BY created_at, snapshot_id
            """,
            (intent_id,),
        ).fetchall()
        snapshots.extend(_provenance_from_row(row) for row in rows)
    return snapshots


def get_session_environment(ledger: LedgerKernel, session_id: str) -> list[EnvironmentSnapshot]:
    ensure_schema(ledger)
    snapshots: list[EnvironmentSnapshot] = []
    for intent_id in list_session_intents(ledger, session_id):
        rows = ledger.connection.execute(
            """
            SELECT snapshot_id
            FROM environment_projection
            WHERE intent_id = ?
            ORDER BY created_at, snapshot_id
            """,
            (intent_id,),
        ).fetchall()
        for (snapshot_id,) in rows:
            snapshot = get_environment(ledger, snapshot_id)
            if snapshot is not None:
                snapshots.append(snapshot)
    return snapshots


def rebuild_session_projection(ledger: LedgerKernel) -> None:
    ensure_schema(ledger)
    rows = ledger.connection.execute(
        """
        SELECT id, event_type, payload_json, created_at
        FROM ledger_events
        WHERE event_type IN (?, ?)
        ORDER BY rowid
        """,
        (SESSION_CREATED_EVENT, SESSION_INTENT_ADDED_EVENT),
    ).fetchall()

    session_created_at: dict[str, str] = {}
    session_last_event_id: dict[str, str] = {}
    memberships: dict[str, list[tuple[str, int, str, str]]] = {}
    seen_memberships: set[tuple[str, str]] = set()

    for event_id, event_type, payload_json, created_at in rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        session_id = _payload_session_id(payload)
        if session_id is None:
            continue
        if event_type == SESSION_CREATED_EVENT:
            session_created_at.setdefault(session_id, created_at)
            session_last_event_id[session_id] = event_id
            memberships.setdefault(session_id, [])
            continue

        intent_id = _payload_intent_id(payload)
        if intent_id is None or session_id not in session_created_at:
            continue
        key = (session_id, intent_id)
        if key in seen_memberships:
            continue
        seen_memberships.add(key)
        order = len(memberships.setdefault(session_id, [])) + 1
        memberships[session_id].append((intent_id, order, created_at, event_id))
        session_last_event_id[session_id] = event_id

    with ledger.connection:
        ledger.connection.execute("DELETE FROM session_membership_projection")
        ledger.connection.execute("DELETE FROM session_projection")
        for session_id in sorted(session_created_at):
            items = memberships.get(session_id, [])
            ledger.connection.execute(
                """
                INSERT INTO session_projection
                    (session_id, intent_count, created_at, last_event_id)
                VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    len(items),
                    session_created_at[session_id],
                    session_last_event_id[session_id],
                ),
            )
            for intent_id, order, created_at, event_id in items:
                ledger.connection.execute(
                    """
                    INSERT INTO session_membership_projection
                        (session_id, intent_id, membership_order, created_at, last_event_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, intent_id, order, created_at, event_id),
                )


class SessionKernel:
    """Session grouping API over one ledger."""

    def __init__(self, ledger: LedgerKernel):
        self._ledger = ledger
        ensure_schema(self._ledger)

    def create_session(self) -> SessionBundle:
        return create_session(self._ledger)

    def add_intent_to_session(self, session_id: str, intent_id: str) -> SessionResult:
        return add_intent_to_session(self._ledger, session_id, intent_id)

    def get_session(self, session_id: str) -> SessionBundle | None:
        return get_session(self._ledger, session_id)

    def list_session_intents(self, session_id: str) -> list[str]:
        return list_session_intents(self._ledger, session_id)

    def get_session_receipts(self, session_id: str) -> list[ReceiptBundle]:
        return get_session_receipts(self._ledger, session_id)

    def get_session_artifacts(self, session_id: str) -> list[ArtifactDescriptor]:
        return get_session_artifacts(self._ledger, session_id)

    def get_session_provenance(self, session_id: str) -> list[ProvenanceSnapshot]:
        return get_session_provenance(self._ledger, session_id)

    def get_session_environment(self, session_id: str) -> list[EnvironmentSnapshot]:
        return get_session_environment(self._ledger, session_id)
