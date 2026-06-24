"""Leira v0.8 auditor: a machine that verifies, not one that repairs.

What this is
-------------
``audit()`` reads the ledger and the projection table exactly as they
stand, recomputes what *should* be true from history alone, and
reports every place reality disagrees with itself. It never writes,
never calls ``rebuild_projection()``, and never fixes anything it
finds. An auditor's only output is a receipt: this chain is valid or it
is not; this projection agrees with history or it does not; here is
exactly where they diverged.

Two independent things are checked, and the result keeps them separate:

  - ``chain_valid`` -- is the hash chain itself intact? This reuses
    ``LedgerKernel.validate_chain()`` (still a fully independent, public
    API of its own -- auditing does not replace it, only consumes its
    verdict as one input among several).
  - ``projections_valid`` -- does ``operation_state_projection`` agree
    with what the ledger implies? The expected projection is
    recomputed *in Python, in memory, from the already-loaded ledger
    events* -- never by calling ``rebuild_projection()``, and never
    written back anywhere. If the two disagree, the ledger is treated
    as correct; the disagreement is reported, not resolved.

Beyond those two headline flags, ``audit()`` also walks the ledger for
structural corruption that ``validate_chain()`` alone would not catch:
duplicate event ids, missing required fields, missing ``run_id`` on
run-scoped events, lifecycle transitions that skip or reorder states,
and artifact payloads that don't match their declared schema. All of
this is read-only; every check is a SELECT, never a write.

v0.9 adds the same treatment for intents: ``inbox_entries`` and
``intent_projection`` (see ``leira.inbox.inbox``) are checked against
``intent_submitted``/``intent_rejected`` ledger events exactly the way
``operation_state_projection`` is checked against run-lifecycle events
-- expected state recomputed in memory from already-loaded events,
compared read-only against the real tables, disagreement reported and
never repaired.

Error codes are deterministic strings of the form ``CODE:identifier``
(e.g. ``"MISSING_RUN_ID:<event_id>"``). The same corruption, audited
twice, always produces the exact same list in the exact same order --
checks run in a fixed sequence, and within each check, events are
walked in ledger insertion order (``rowid``).

What this explicitly does NOT do
-----------------------------------
No repair, no automatic mutation, no rebuild during audit, no anomaly
scoring, no monitoring loop, no LLM explanation of what went wrong.
Workers think. Kernel gates. Ledger remembers. Projections serve.
Auditors verify. Truth lives in history; repair belongs elsewhere.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import ALLOWED_TRANSITIONS
from leira.inbox.inbox import INTENT_LEDGER_EVENT_TYPES
from leira.projection.state import RUN_LIFECYCLE_EVENT_TYPES

REQUIRED_EVENT_FIELDS = (
    "id",
    "parent_event_hash",
    "event_type",
    "worker_id",
    "payload_json",
    "event_hash",
    "created_at",
)


@dataclass(frozen=True)
class AuditResult:
    success: bool
    chain_valid: bool
    projections_valid: bool
    errors: list[str] = field(default_factory=list)


def _load_events(ledger: LedgerKernel) -> list[dict]:
    """One read-only pass over the ledger, in insertion order."""
    rows = ledger.connection.execute(
        """
        SELECT id, operation_id, parent_event_hash, event_type, worker_id,
               payload_json, artifact_hash, event_hash, created_at
        FROM ledger_events
        ORDER BY rowid
        """
    ).fetchall()
    return [
        {
            "id": row[0],
            "operation_id": row[1],
            "parent_event_hash": row[2],
            "event_type": row[3],
            "worker_id": row[4],
            "payload_json": row[5],
            "artifact_hash": row[6],
            "event_hash": row[7],
            "created_at": row[8],
        }
        for row in rows
    ]


def _parse_payload(event: dict) -> dict | None:
    try:
        payload = json.loads(event["payload_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _event_run_id(event: dict) -> str | None:
    payload = _parse_payload(event)
    if payload is None:
        return None
    run_id = payload.get("run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def check_required_fields(events: list[dict]) -> list[str]:
    """Every event must have every required column populated."""
    errors: list[str] = []
    for event in events:
        for name in REQUIRED_EVENT_FIELDS:
            value = event.get(name)
            if value is None or value == "":
                errors.append(f"MISSING_REQUIRED_FIELD:{event.get('id')}:{name}")
    return errors


def check_duplicate_event_ids(events: list[dict]) -> list[str]:
    """No event id may appear twice. (Blocked by schema in normal operation;
    this guards against a ledger assembled or edited outside that schema.)"""
    errors: list[str] = []
    seen: set[str] = set()
    for event in events:
        event_id = event["id"]
        if event_id in seen:
            errors.append(f"DUPLICATE_EVENT_ID:{event_id}")
        seen.add(event_id)
    return errors


def check_monotonic_ordering(events: list[dict]) -> list[str]:
    """created_at must never go backwards across insertion order."""
    errors: list[str] = []
    previous_created_at: str | None = None
    for event in events:
        created_at = event["created_at"]
        if previous_created_at is not None and created_at < previous_created_at:
            errors.append(f"EVENT_ORDERING_NOT_MONOTONIC:{event['id']}")
        previous_created_at = created_at
    return errors


def check_run_id_presence(events: list[dict]) -> list[str]:
    """Run-scoped lifecycle events must carry a run_id in their payload."""
    errors: list[str] = []
    for event in events:
        if event["event_type"] not in RUN_LIFECYCLE_EVENT_TYPES:
            continue
        if _event_run_id(event) is None:
            errors.append(f"MISSING_RUN_ID:{event['id']}")
    return errors


def check_lifecycle_transitions(events: list[dict]) -> list[str]:
    """Replay each run's lifecycle events and verify every transition is legal.

    Reuses leira.dispatcher.lifecycle.ALLOWED_TRANSITIONS directly --
    the same table the live system already enforces on write -- rather
    than redefining the rules here. A run's first event must be
    run_created; anything else as the very first event for a run_id is
    already illegal.
    """
    errors: list[str] = []
    last_state_by_run: dict[str, str] = {}
    for event in events:
        event_type = event["event_type"]
        if event_type not in RUN_LIFECYCLE_EVENT_TYPES:
            continue
        run_id = _event_run_id(event)
        if run_id is None:
            continue  # already reported by check_run_id_presence

        previous_state = last_state_by_run.get(run_id)
        if previous_state is None:
            if event_type != "run_created":
                errors.append(f"ILLEGAL_TRANSITION:{event['id']}")
        elif event_type not in ALLOWED_TRANSITIONS.get(previous_state, []):
            errors.append(f"ILLEGAL_TRANSITION:{event['id']}")

        last_state_by_run[run_id] = event_type
    return errors


def check_artifact_schema(events: list[dict]) -> list[str]:
    """artifact_written events must carry a well-formed artifact for a real run."""
    errors: list[str] = []
    known_run_ids = {
        run_id
        for event in events
        if event["event_type"] == "run_created"
        for run_id in (_event_run_id(event),)
        if run_id is not None
    }

    for event in events:
        if event["event_type"] != "artifact_written":
            continue
        payload = _parse_payload(event)
        if payload is None:
            errors.append(f"ARTIFACT_SCHEMA_INVALID:{event['id']}")
            continue
        run_id = payload.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id not in known_run_ids:
            errors.append(f"ARTIFACT_SCHEMA_INVALID:{event['id']}")
            continue
        artifact = payload.get("artifact")
        if (
            not isinstance(artifact, dict)
            or not isinstance(artifact.get("type"), str)
            or "content" not in artifact
        ):
            errors.append(f"ARTIFACT_SCHEMA_INVALID:{event['id']}")
    return errors


def compute_expected_projection(events: list[dict]) -> dict[str, tuple[str, str, str]]:
    """Recompute, in memory only, what operation_state_projection should say.

    Identical in spirit to rebuild_projection()'s algorithm but never
    touches the database and is never called by rebuild_projection() or
    vice versa -- this is the auditor's own independent recomputation,
    used purely for comparison.
    """
    expected: dict[str, tuple[str, str, str]] = {}
    for event in events:
        event_type = event["event_type"]
        if event_type not in RUN_LIFECYCLE_EVENT_TYPES:
            continue
        run_id = _event_run_id(event)
        if run_id is None:
            continue
        expected[run_id] = (event_type, event["id"], event["created_at"])
    return expected


def check_projections(ledger: LedgerKernel, events: list[dict]) -> list[str]:
    """Compare the real projection table against the in-memory expected one.

    Read-only: a single SELECT against operation_state_projection. If
    the table does not exist at all, every expected run is reported as
    missing -- that is still a disagreement to report, not a reason to
    create the table.
    """
    expected = compute_expected_projection(events)

    try:
        actual_rows = ledger.connection.execute(
            "SELECT run_id, current_state, last_event_id, updated_at "
            "FROM operation_state_projection"
        ).fetchall()
    except sqlite3.OperationalError:
        actual_rows = []
    actual = {row[0]: (row[1], row[2], row[3]) for row in actual_rows}

    errors: list[str] = []
    for run_id in sorted(expected):
        expected_state, expected_event_id, expected_updated_at = expected[run_id]
        actual_entry = actual.get(run_id)

        if actual_entry is None:
            errors.append(f"PROJECTION_MISMATCH:{run_id}")
            continue

        actual_state, actual_event_id, actual_updated_at = actual_entry
        if actual_state != expected_state:
            errors.append(f"PROJECTION_MISMATCH:{run_id}")
        if actual_event_id != expected_event_id:
            errors.append(f"PROJECTION_LAST_EVENT_ID_MISMATCH:{run_id}")
        if actual_updated_at != expected_updated_at:
            errors.append(f"PROJECTION_UPDATED_AT_MISMATCH:{run_id}")

    return errors


def compute_expected_intents(events: list[dict]) -> dict[str, tuple[str, str, str]]:
    """Recompute, in memory only, what each intent's inbox/projection row should say.

    Mirrors compute_expected_projection()'s shape for run lifecycles,
    but for intent_submitted/intent_rejected events: (status,
    last_event_id, updated_at) per intent_id, derived purely from
    already-loaded ledger events.
    """
    expected: dict[str, tuple[str, str, str]] = {}
    for event in events:
        if event["event_type"] not in INTENT_LEDGER_EVENT_TYPES:
            continue
        payload = _parse_payload(event)
        if payload is None:
            continue
        intent_id = payload.get("intent_id")
        status = payload.get("status")
        if not isinstance(intent_id, str) or not intent_id or not isinstance(status, str):
            continue
        expected[intent_id] = (status, event["id"], event["created_at"])
    return expected


def check_intents(ledger: LedgerKernel, events: list[dict]) -> list[str]:
    """Compare inbox_entries and intent_projection against the ledger.

    Read-only: two SELECTs against inbox_entries and intent_projection.
    If either table doesn't exist, every expected intent is reported
    missing from it -- a disagreement to report, not a reason to
    create the table.
    """
    expected = compute_expected_intents(events)

    try:
        inbox_rows = ledger.connection.execute(
            "SELECT intent_id, status FROM inbox_entries"
        ).fetchall()
    except sqlite3.OperationalError:
        inbox_rows = []
    inbox = {row[0]: row[1] for row in inbox_rows}

    try:
        projection_rows = ledger.connection.execute(
            "SELECT intent_id, status, last_event_id, updated_at FROM intent_projection"
        ).fetchall()
    except sqlite3.OperationalError:
        projection_rows = []
    projection = {row[0]: (row[1], row[2], row[3]) for row in projection_rows}

    errors: list[str] = []
    for intent_id in sorted(expected):
        expected_status, expected_event_id, expected_updated_at = expected[intent_id]

        inbox_status = inbox.get(intent_id)
        if inbox_status is None:
            errors.append(f"MISSING_INBOX_ROW:{intent_id}")
        elif inbox_status != expected_status:
            errors.append(f"INTENT_STATUS_MISMATCH:{intent_id}")

        projection_entry = projection.get(intent_id)
        if projection_entry is None:
            errors.append(f"INTENT_PROJECTION_MISMATCH:{intent_id}")
            continue

        actual_status, actual_event_id, actual_updated_at = projection_entry
        if actual_status != expected_status:
            errors.append(f"INTENT_PROJECTION_MISMATCH:{intent_id}")
        if actual_event_id != expected_event_id:
            errors.append(f"INTENT_PROJECTION_LAST_EVENT_ID_MISMATCH:{intent_id}")
        if actual_updated_at != expected_updated_at:
            errors.append(f"INTENT_PROJECTION_UPDATED_AT_MISMATCH:{intent_id}")

    return errors


def audit(ledger: LedgerKernel) -> AuditResult:
    """Read the ledger and the projection table and report every disagreement.

    Read-only end to end: every check below is a SELECT against data
    already loaded once from the ledger (or, for projections, one
    additional read-only SELECT). Nothing is written, nothing is
    rebuilt, nothing is repaired. Checks run in a fixed order so that
    identical corruption always produces an identical error list.
    """
    errors: list[str] = []

    chain_result = ledger.validate_chain()
    chain_valid = chain_result.success
    if not chain_valid:
        code = "MISSING_PREVIOUS_HASH" if chain_result.error_type == "CHAIN_BROKEN" else "BROKEN_HASH_CHAIN"
        errors.append(f"{code}:{chain_result.failed_event_id}")

    events = _load_events(ledger)

    errors.extend(check_required_fields(events))
    errors.extend(check_duplicate_event_ids(events))
    errors.extend(check_monotonic_ordering(events))
    errors.extend(check_run_id_presence(events))
    errors.extend(check_lifecycle_transitions(events))
    errors.extend(check_artifact_schema(events))

    projection_errors = check_projections(ledger, events)
    errors.extend(projection_errors)

    intent_errors = check_intents(ledger, events)
    errors.extend(intent_errors)

    projections_valid = len(projection_errors) == 0 and len(intent_errors) == 0

    return AuditResult(
        success=len(errors) == 0,
        chain_valid=chain_valid,
        projections_valid=projections_valid,
        errors=errors,
    )
