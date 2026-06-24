"""Leira v1.4 filesystem workspace: an evidence locker, not storage sync."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from leira.dispatcher.kernel import LedgerKernel

from .hashing import sha256
from .paths import WorkspaceError, _get_artifact_path, normalize_relative_path

WORKSPACE_WORKER_ID = "kernel"
ARTIFACT_FILE_WRITTEN_EVENT = "artifact_file_written"
ARTIFACT_WRITE_REJECTED_EVENT = "artifact_write_rejected"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact_projection (
    artifact_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ArtifactDescriptor:
    artifact_id: str
    intent_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    created_at: str


def ensure_schema(ledger: LedgerKernel) -> None:
    ledger.connection.executescript(_SCHEMA)
    ledger.connection.commit()


def _artifact_payload(
    *,
    artifact_id: str,
    intent_id: str,
    relative_path: str,
    digest: str,
    size_bytes: int,
) -> dict:
    return {
        "type": "artifact_file",
        "content": {
            "artifact_id": artifact_id,
            "intent_id": intent_id,
            "relative_path": relative_path,
            "sha256": digest,
            "size_bytes": size_bytes,
        },
    }


def _rejection_payload(intent_id: str, relative_path: str, error_type: str) -> dict:
    return {
        "intent_id": intent_id,
        "relative_path": relative_path,
        "error_type": error_type,
    }


def _upsert_artifact_projection(
    ledger: LedgerKernel, descriptor: ArtifactDescriptor, last_event_id: str
) -> bool:
    try:
        ensure_schema(ledger)
        with ledger.connection:
            ledger.connection.execute(
                """
                INSERT INTO artifact_projection
                    (artifact_id, intent_id, relative_path, sha256, size_bytes, created_at, last_event_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    intent_id = excluded.intent_id,
                    relative_path = excluded.relative_path,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    created_at = excluded.created_at,
                    last_event_id = excluded.last_event_id
                """,
                (
                    descriptor.artifact_id,
                    descriptor.intent_id,
                    descriptor.relative_path,
                    descriptor.sha256,
                    descriptor.size_bytes,
                    descriptor.created_at,
                    last_event_id,
                ),
            )
        return True
    except sqlite3.Error:
        return False


def descriptor_from_projection_row(row: tuple) -> ArtifactDescriptor:
    return ArtifactDescriptor(
        artifact_id=row[0],
        intent_id=row[1],
        relative_path=row[2],
        sha256=row[3],
        size_bytes=row[4],
        created_at=row[5],
    )


class Workspace:
    """Write-once file artifact store backed by ledger metadata."""

    def __init__(self, ledger: LedgerKernel, workspace_root: str | Path):
        self._ledger = ledger
        self._root = Path(workspace_root)
        ensure_schema(self._ledger)

    @property
    def root(self) -> Path:
        return self._root

    def _get_artifact_path(self, intent_id: str, relative_path: str) -> Path:
        return _get_artifact_path(self._root, intent_id, relative_path)

    def _record_rejection(self, intent_id: str, relative_path: str, error_type: str) -> None:
        self._ledger.append_event(
            event_type=ARTIFACT_WRITE_REJECTED_EVENT,
            worker_id=WORKSPACE_WORKER_ID,
            payload=_rejection_payload(intent_id, relative_path, error_type),
            operation_id=intent_id,
        )

    def write_artifact(
        self, intent_id: str, relative_path: str, content: bytes
    ) -> ArtifactDescriptor:
        if not isinstance(content, bytes):
            raise WorkspaceError("INVALID_CONTENT", "content must be bytes")
        try:
            normalized_relative_path = normalize_relative_path(relative_path)
            path = self._get_artifact_path(intent_id, relative_path)
        except WorkspaceError as exc:
            self._record_rejection(intent_id, relative_path, exc.error_type)
            raise

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "xb") as handle:
                handle.write(content)
        except FileExistsError as exc:
            self._record_rejection(intent_id, relative_path, "ALREADY_EXISTS")
            raise WorkspaceError("ALREADY_EXISTS", "artifact file already exists") from exc

        digest = sha256(content)
        artifact_id = str(uuid.uuid4())
        append_result = self._ledger.append_event(
            event_type=ARTIFACT_FILE_WRITTEN_EVENT,
            worker_id=WORKSPACE_WORKER_ID,
            payload=_artifact_payload(
                artifact_id=artifact_id,
                intent_id=intent_id,
                relative_path=normalized_relative_path,
                digest=digest,
                size_bytes=len(content),
            ),
            artifact_hash=digest,
            operation_id=intent_id,
        )
        if not append_result.success:
            raise WorkspaceError(
                append_result.error_type or "LEDGER_APPEND_FAILED",
                append_result.message or "artifact write could not be recorded",
            )

        descriptor = ArtifactDescriptor(
            artifact_id=artifact_id,
            intent_id=intent_id,
            relative_path=normalized_relative_path,
            sha256=digest,
            size_bytes=len(content),
            created_at=append_result.created_at or "",
        )
        _upsert_artifact_projection(self._ledger, descriptor, append_result.event_id or "")
        return descriptor

    def read_artifact(self, intent_id: str, relative_path: str) -> bytes:
        path = self._get_artifact_path(intent_id, relative_path)
        return path.read_bytes()

    def get_artifact(self, artifact_id: str) -> ArtifactDescriptor | None:
        row = self._ledger.connection.execute(
            "SELECT artifact_id, intent_id, relative_path, sha256, size_bytes, created_at "
            "FROM artifact_projection WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        return descriptor_from_projection_row(row) if row else None


def parse_artifact_descriptor_event(event: dict) -> tuple[ArtifactDescriptor, str] | None:
    if event["event_type"] != ARTIFACT_FILE_WRITTEN_EVENT:
        return None
    try:
        payload = json.loads(event["payload_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != "artifact_file":
        return None
    content = payload.get("content")
    if not isinstance(content, dict):
        return None
    required = ("artifact_id", "intent_id", "relative_path", "sha256", "size_bytes")
    if any(name not in content for name in required):
        return None
    descriptor = ArtifactDescriptor(
        artifact_id=content["artifact_id"],
        intent_id=content["intent_id"],
        relative_path=content["relative_path"],
        sha256=content["sha256"],
        size_bytes=content["size_bytes"],
        created_at=event["created_at"],
    )
    return descriptor, event["id"]


def rebuild_artifact_projection(ledger: LedgerKernel) -> None:
    ensure_schema(ledger)
    rows = ledger.connection.execute(
        """
        SELECT id, event_type, payload_json, created_at
        FROM ledger_events
        WHERE event_type = ?
        ORDER BY rowid
        """,
        (ARTIFACT_FILE_WRITTEN_EVENT,),
    ).fetchall()
    events = [
        {"id": row[0], "event_type": row[1], "payload_json": row[2], "created_at": row[3]}
        for row in rows
    ]

    with ledger.connection:
        ledger.connection.execute("DELETE FROM artifact_projection")
        for event in events:
            parsed = parse_artifact_descriptor_event(event)
            if parsed is None:
                continue
            descriptor, last_event_id = parsed
            ledger.connection.execute(
                """
                INSERT INTO artifact_projection
                    (artifact_id, intent_id, relative_path, sha256, size_bytes, created_at, last_event_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    descriptor.artifact_id,
                    descriptor.intent_id,
                    descriptor.relative_path,
                    descriptor.sha256,
                    descriptor.size_bytes,
                    descriptor.created_at,
                    last_event_id,
                ),
            )


def descriptor_to_dict(descriptor: ArtifactDescriptor) -> dict:
    return asdict(descriptor)
