"""Leira v1.7 archive and replay: portable history, not backup magic."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from leira.dispatcher.kernel import LedgerKernel
from leira.workspace.hashing import sha256
from leira.workspace.paths import _get_artifact_path

from .replay import replay_history

_LEDGER_COLUMNS = (
    "id",
    "operation_id",
    "parent_event_hash",
    "event_type",
    "worker_id",
    "payload_json",
    "artifact_hash",
    "event_hash",
    "created_at",
)


@dataclass(frozen=True)
class ArchiveBundle:
    created_at: str
    first_event_id: str
    last_event_id: str
    event_count: int
    archive_sha256: str


class ArchiveError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type


def _canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes())


def _hash_named_blobs(blobs: list[tuple[str, bytes]]) -> str:
    import hashlib

    h = hashlib.sha256()
    for name, content in sorted(blobs, key=lambda item: item[0]):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(content)
        h.update(b"\0")
    return h.hexdigest()


def _workspace_digest(root: Path) -> str:
    if not root.exists():
        return _hash_named_blobs([])
    blobs: list[tuple[str, bytes]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        blobs.append((path.relative_to(root).as_posix(), path.read_bytes()))
    return _hash_named_blobs(blobs)


def _archive_digest(archive_root: Path) -> str:
    blobs: list[tuple[str, bytes]] = []
    for path in sorted(p for p in archive_root.rglob("*") if p.is_file()):
        blobs.append((path.relative_to(archive_root).as_posix(), path.read_bytes()))
    return _hash_named_blobs(blobs)


def _load_events(ledger: LedgerKernel) -> list[dict]:
    rows = ledger.connection.execute(
        """
        SELECT id, operation_id, parent_event_hash, event_type, worker_id,
               payload_json, artifact_hash, event_hash, created_at
        FROM ledger_events
        ORDER BY rowid
        """
    ).fetchall()
    return [dict(zip(_LEDGER_COLUMNS, row)) for row in rows]


def _ledger_jsonl(events: list[dict]) -> bytes:
    return "".join(_canonical_json(event) + "\n" for event in events).encode("utf-8")


def _artifact_descriptors(ledger: LedgerKernel) -> list[tuple[str, str, str, int]]:
    rows = ledger.connection.execute(
        """
        SELECT intent_id, relative_path, sha256, size_bytes
        FROM artifact_projection
        ORDER BY intent_id, relative_path
        """
    ).fetchall()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def _copy_workspace_files(
    ledger: LedgerKernel, source_workspace: Path, archive_workspace: Path
) -> None:
    for intent_id, relative_path, expected_sha, expected_size in _artifact_descriptors(ledger):
        source = _get_artifact_path(source_workspace, intent_id, relative_path)
        if not source.exists() or not source.is_file():
            raise ArchiveError("MISSING_ARTIFACT_FILE", str(source))
        content = source.read_bytes()
        if len(content) != expected_size:
            raise ArchiveError("SIZE_MISMATCH", str(source))
        if sha256(content) != expected_sha:
            raise ArchiveError("HASH_MISMATCH", str(source))
        destination = _get_artifact_path(archive_workspace, intent_id, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "xb") as handle:
            handle.write(content)


def export_archive(
    ledger: LedgerKernel,
    workspace_root: str | Path,
    output_path: str | Path,
    *,
    created_at: str | None = None,
) -> ArchiveBundle:
    output = Path(output_path)
    if output.exists() and any(output.iterdir()):
        raise ArchiveError("ARCHIVE_EXISTS", "output archive directory is not empty")
    output.mkdir(parents=True, exist_ok=True)

    events = _load_events(ledger)
    if not events:
        raise ArchiveError("EMPTY_LEDGER", "cannot archive an empty ledger")
    ledger_bytes = _ledger_jsonl(events)
    ledger_sha = sha256(ledger_bytes)

    ledger_path = output / "ledger_events.jsonl"
    with open(ledger_path, "xb") as handle:
        handle.write(ledger_bytes)

    archive_workspace = output / "workspace"
    _copy_workspace_files(ledger, Path(workspace_root), archive_workspace)
    workspace_sha = _workspace_digest(archive_workspace)

    manifest = {
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "first_event_id": events[0]["id"],
        "last_event_id": events[-1]["id"],
        "event_count": len(events),
        "ledger_events_sha256": ledger_sha,
        "workspace_sha256": workspace_sha,
    }
    manifest_path = output / "manifest.json"
    with open(manifest_path, "xb") as handle:
        handle.write(_canonical_json(manifest).encode("utf-8"))

    return ArchiveBundle(
        created_at=manifest["created_at"],
        first_event_id=manifest["first_event_id"],
        last_event_id=manifest["last_event_id"],
        event_count=manifest["event_count"],
        archive_sha256=_archive_digest(output),
    )


def _read_manifest(archive_root: Path) -> dict:
    manifest_path = archive_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveError("INVALID_MANIFEST", str(exc)) from exc
    if not isinstance(manifest, dict):
        raise ArchiveError("INVALID_MANIFEST", "manifest must be a JSON object")
    return manifest


def _read_ledger_jsonl(archive_root: Path, expected_sha: str) -> list[dict]:
    path = archive_root / "ledger_events.jsonl"
    content = path.read_bytes()
    actual_sha = sha256(content)
    if actual_sha != expected_sha:
        raise ArchiveError("LEDGER_SHA_MISMATCH", "ledger_events.jsonl hash mismatch")
    events: list[dict] = []
    for line in content.decode("utf-8").splitlines():
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ArchiveError("INVALID_LEDGER_EVENT", "ledger event line must be an object")
        events.append(event)
    return events


def _verify_manifest(manifest: dict, events: list[dict]) -> None:
    if manifest.get("event_count") != len(events):
        raise ArchiveError("EVENT_COUNT_MISMATCH", "manifest event_count does not match JSONL")
    if events:
        if manifest.get("first_event_id") != events[0].get("id"):
            raise ArchiveError("FIRST_EVENT_ID_MISMATCH", "manifest first_event_id mismatch")
        if manifest.get("last_event_id") != events[-1].get("id"):
            raise ArchiveError("LAST_EVENT_ID_MISMATCH", "manifest last_event_id mismatch")


def _import_ledger_events(ledger: LedgerKernel, events: list[dict]) -> None:
    if ledger.connection.execute("SELECT 1 FROM ledger_events LIMIT 1").fetchone():
        raise ArchiveError("LEDGER_NOT_EMPTY", "target ledger already contains events")
    with ledger.connection:
        for event in events:
            values = tuple(event.get(column) for column in _LEDGER_COLUMNS)
            if any(value is None and column not in ("operation_id", "artifact_hash") for column, value in zip(_LEDGER_COLUMNS, values)):
                raise ArchiveError("INVALID_LEDGER_EVENT", "ledger event missing required field")
            ledger.connection.execute(
                """
                INSERT INTO ledger_events (
                    id, operation_id, parent_event_hash, event_type, worker_id,
                    payload_json, artifact_hash, event_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )


def _restore_workspace_files(
    ledger: LedgerKernel, archive_workspace: Path, target_workspace: Path
) -> None:
    rows = ledger.connection.execute(
        """
        SELECT intent_id, relative_path, sha256, size_bytes
        FROM artifact_projection
        ORDER BY intent_id, relative_path
        """
    ).fetchall()
    for intent_id, relative_path, expected_sha, expected_size in rows:
        source = _get_artifact_path(archive_workspace, intent_id, relative_path)
        if not source.exists() or not source.is_file():
            raise ArchiveError("MISSING_ARCHIVE_ARTIFACT", str(source))
        content = source.read_bytes()
        if len(content) != expected_size or sha256(content) != expected_sha:
            raise ArchiveError("ARCHIVE_ARTIFACT_HASH_MISMATCH", str(source))
        destination = _get_artifact_path(target_workspace, intent_id, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != content:
                raise ArchiveError("TARGET_ARTIFACT_EXISTS", str(destination))
            continue
        with open(destination, "xb") as handle:
            handle.write(content)
        if sha256(destination.read_bytes()) != expected_sha:
            raise ArchiveError("IMPORTED_ARTIFACT_HASH_MISMATCH", str(destination))


def import_archive(
    ledger: LedgerKernel,
    workspace_root: str | Path,
    archive_path: str | Path,
) -> ArchiveBundle:
    archive_root = Path(archive_path)
    manifest = _read_manifest(archive_root)
    events = _read_ledger_jsonl(archive_root, manifest["ledger_events_sha256"])
    _verify_manifest(manifest, events)
    if _workspace_digest(archive_root / "workspace") != manifest.get("workspace_sha256"):
        raise ArchiveError("WORKSPACE_SHA_MISMATCH", "archive workspace hash mismatch")

    _import_ledger_events(ledger, events)
    if not ledger.validate_chain().success:
        raise ArchiveError("BROKEN_HASH_CHAIN", "imported ledger failed validate_chain")

    replay_history(ledger)
    _restore_workspace_files(ledger, archive_root / "workspace", Path(workspace_root))
    audit_result = replay_history(ledger, workspace_root)
    if not audit_result.success:
        raise ArchiveError("AUDIT_FAILED", ",".join(audit_result.errors))

    return ArchiveBundle(
        created_at=manifest["created_at"],
        first_event_id=manifest["first_event_id"],
        last_event_id=manifest["last_event_id"],
        event_count=manifest["event_count"],
        archive_sha256=_archive_digest(archive_root),
    )
