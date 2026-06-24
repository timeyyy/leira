"""Leira v1.8 environment snapshots: runtime context, not deployment."""

from __future__ import annotations

import json
import platform as platform_module
import sys
import uuid
from dataclasses import asdict, dataclass
from importlib import metadata as importlib_metadata

from leira.dispatcher.kernel import LedgerKernel, PayloadValidationError, canonicalize_payload

ENVIRONMENT_WORKER_ID = "kernel"
ENVIRONMENT_CAPTURED_EVENT = "environment_captured"
ENVIRONMENT_CAPTURE_FAILED_EVENT = "environment_capture_failed"
ENVIRONMENT_EVENT_TYPES = frozenset(
    {ENVIRONMENT_CAPTURED_EVENT, ENVIRONMENT_CAPTURE_FAILED_EVENT}
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS environment_projection (
    snapshot_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    python_version TEXT NOT NULL,
    platform TEXT NOT NULL,
    executable TEXT NOT NULL,
    created_at TEXT NOT NULL,
    error_type TEXT,
    last_event_id TEXT NOT NULL
);
"""

MAX_ENVIRONMENT_PAYLOAD_BYTES = 1024 * 1024


@dataclass(frozen=True)
class PackageInfo:
    name: str
    version: str


@dataclass(frozen=True)
class EnvironmentSnapshot:
    snapshot_id: str
    intent_id: str
    python_version: str
    platform: str
    executable: str
    installed_packages: list[PackageInfo]
    created_at: str
    error_type: str | None = None
    stderr: str = ""


def ensure_schema(ledger: LedgerKernel) -> None:
    ledger.connection.executescript(_SCHEMA)
    ledger.connection.commit()


def _snapshot_payload(snapshot: EnvironmentSnapshot) -> dict:
    return {
        "type": "environment",
        "content": {
            "snapshot_id": snapshot.snapshot_id,
            "intent_id": snapshot.intent_id,
            "python_version": snapshot.python_version,
            "platform": snapshot.platform,
            "executable": snapshot.executable,
            "installed_packages": [asdict(package) for package in snapshot.installed_packages],
            "error_type": snapshot.error_type,
            "stderr": snapshot.stderr,
        },
    }


def _capture_packages() -> list[PackageInfo]:
    packages: list[PackageInfo] = []
    for distribution in importlib_metadata.distributions():
        name = distribution.metadata["Name"]
        packages.append(PackageInfo(name=name, version=distribution.version))
    return sorted(packages, key=lambda package: (package.name.lower(), package.version))


def _capture_snapshot_values(snapshot_id: str, intent_id: str) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        snapshot_id=snapshot_id,
        intent_id=intent_id,
        python_version=sys.version,
        platform=platform_module.platform(),
        executable=sys.executable,
        installed_packages=_capture_packages(),
        created_at="",
        error_type=None,
        stderr="",
    )


def _failure_snapshot(
    *, snapshot_id: str, intent_id: str, error_type: str, stderr: str
) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        snapshot_id=snapshot_id,
        intent_id=intent_id,
        python_version="",
        platform="",
        executable="",
        installed_packages=[],
        created_at="",
        error_type=error_type,
        stderr=stderr,
    )


def _row_to_snapshot(row: tuple) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        snapshot_id=row[0],
        intent_id=row[1],
        python_version=row[2],
        platform=row[3],
        executable=row[4],
        installed_packages=[],
        created_at=row[5],
        error_type=row[6],
    )


def _insert_projection(
    ledger: LedgerKernel, snapshot: EnvironmentSnapshot, last_event_id: str
) -> bool:
    try:
        ensure_schema(ledger)
        with ledger.connection:
            ledger.connection.execute(
                """
                INSERT INTO environment_projection
                    (snapshot_id, intent_id, python_version, platform, executable,
                     created_at, error_type, last_event_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    intent_id = excluded.intent_id,
                    python_version = excluded.python_version,
                    platform = excluded.platform,
                    executable = excluded.executable,
                    created_at = excluded.created_at,
                    error_type = excluded.error_type,
                    last_event_id = excluded.last_event_id
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.intent_id,
                    snapshot.python_version,
                    snapshot.platform,
                    snapshot.executable,
                    snapshot.created_at,
                    snapshot.error_type,
                    last_event_id,
                ),
            )
        return True
    except Exception:
        return False


def _append_snapshot(
    ledger: LedgerKernel, event_type: str, draft: EnvironmentSnapshot
) -> EnvironmentSnapshot:
    append_result = ledger.append_event(
        event_type=event_type,
        worker_id=ENVIRONMENT_WORKER_ID,
        payload=_snapshot_payload(draft),
        operation_id=draft.intent_id,
    )
    if not append_result.success:
        return _failure_snapshot(
            snapshot_id=draft.snapshot_id,
            intent_id=draft.intent_id,
            error_type="STORAGE_FAILURE",
            stderr=append_result.message or "",
        )
    snapshot = EnvironmentSnapshot(
        snapshot_id=draft.snapshot_id,
        intent_id=draft.intent_id,
        python_version=draft.python_version,
        platform=draft.platform,
        executable=draft.executable,
        installed_packages=draft.installed_packages,
        created_at=append_result.created_at or "",
        error_type=draft.error_type,
        stderr=draft.stderr,
    )
    _insert_projection(ledger, snapshot, append_result.event_id or "")
    return snapshot


def capture_environment(ledger: LedgerKernel, intent_id: str) -> EnvironmentSnapshot:
    """Capture the current Python runtime/package context for intent_id."""
    snapshot_id = str(uuid.uuid4())
    try:
        draft = _capture_snapshot_values(snapshot_id, intent_id)
        canonical = canonicalize_payload(_snapshot_payload(draft))
        if len(canonical.encode("utf-8")) > MAX_ENVIRONMENT_PAYLOAD_BYTES:
            draft = _failure_snapshot(
                snapshot_id=snapshot_id,
                intent_id=intent_id,
                error_type="ARTIFACT_TOO_LARGE",
                stderr="environment payload exceeded size limit",
            )
            return _append_snapshot(ledger, ENVIRONMENT_CAPTURE_FAILED_EVENT, draft)
        return _append_snapshot(ledger, ENVIRONMENT_CAPTURED_EVENT, draft)
    except (Exception, PayloadValidationError) as exc:
        draft = _failure_snapshot(
            snapshot_id=snapshot_id,
            intent_id=intent_id,
            error_type="UNEXPECTED",
            stderr=str(exc),
        )
        return _append_snapshot(ledger, ENVIRONMENT_CAPTURE_FAILED_EVENT, draft)


def _packages_from_content(content: dict) -> list[PackageInfo]:
    packages: list[PackageInfo] = []
    for item in content.get("installed_packages", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("version"), str):
            packages.append(PackageInfo(name=item["name"], version=item["version"]))
    return packages


def parse_environment_event(event: dict) -> tuple[EnvironmentSnapshot, str] | None:
    if event["event_type"] not in ENVIRONMENT_EVENT_TYPES:
        return None
    try:
        payload = json.loads(event["payload_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != "environment":
        return None
    content = payload.get("content")
    if not isinstance(content, dict):
        return None
    try:
        snapshot = EnvironmentSnapshot(
            snapshot_id=content["snapshot_id"],
            intent_id=content["intent_id"],
            python_version=content["python_version"],
            platform=content["platform"],
            executable=content["executable"],
            installed_packages=_packages_from_content(content),
            created_at=event["created_at"],
            error_type=content.get("error_type"),
            stderr=content.get("stderr", ""),
        )
    except KeyError:
        return None
    return snapshot, event["id"]


def rebuild_environment_projection(ledger: LedgerKernel) -> None:
    ensure_schema(ledger)
    rows = ledger.connection.execute(
        """
        SELECT id, event_type, payload_json, created_at
        FROM ledger_events
        WHERE event_type IN (?, ?)
        ORDER BY rowid
        """,
        (ENVIRONMENT_CAPTURED_EVENT, ENVIRONMENT_CAPTURE_FAILED_EVENT),
    ).fetchall()
    events = [
        {"id": row[0], "event_type": row[1], "payload_json": row[2], "created_at": row[3]}
        for row in rows
    ]
    with ledger.connection:
        ledger.connection.execute("DELETE FROM environment_projection")
        for event in events:
            parsed = parse_environment_event(event)
            if parsed is None:
                continue
            snapshot, last_event_id = parsed
            ledger.connection.execute(
                """
                INSERT INTO environment_projection
                    (snapshot_id, intent_id, python_version, platform, executable,
                     created_at, error_type, last_event_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.intent_id,
                    snapshot.python_version,
                    snapshot.platform,
                    snapshot.executable,
                    snapshot.created_at,
                    snapshot.error_type,
                    last_event_id,
                ),
            )


def get_environment(
    ledger: LedgerKernel, snapshot_id: str
) -> EnvironmentSnapshot | None:
    ensure_schema(ledger)
    row = ledger.connection.execute(
        """
        SELECT snapshot_id, intent_id, python_version, platform, executable,
               created_at, error_type, last_event_id
        FROM environment_projection
        WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    if row is None:
        return None
    parsed_row = _row_to_snapshot(row)
    event_row = ledger.connection.execute(
        "SELECT id, event_type, payload_json, created_at FROM ledger_events WHERE id = ?",
        (row[7],),
    ).fetchone()
    if event_row is None:
        return parsed_row
    parsed = parse_environment_event(
        {
            "id": event_row[0],
            "event_type": event_row[1],
            "payload_json": event_row[2],
            "created_at": event_row[3],
        }
    )
    return parsed[0] if parsed else parsed_row


class EnvironmentKernel:
    """Small library facade over one ledger's environment snapshots."""

    def __init__(self, ledger: LedgerKernel):
        self._ledger = ledger
        ensure_schema(self._ledger)

    def capture_environment(self, intent_id: str) -> EnvironmentSnapshot:
        return capture_environment(self._ledger, intent_id)

    def get_environment(self, snapshot_id: str) -> EnvironmentSnapshot | None:
        return get_environment(self._ledger, snapshot_id)
