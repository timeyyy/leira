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

v1.0 adds the same treatment for intent *execution*
(``intent_claimed``/``intent_completed``/``intent_failed``, appended
by ``leira.dispatcher.dispatcher.dispatch_once``): transitions are
replayed against ``ALLOWED_INTENT_TRANSITIONS`` exactly as run
transitions are replayed against ``ALLOWED_TRANSITIONS``, duplicate
claims and worker_name inconsistencies are reported, and -- because
``COMPLETED``/``FAILED``/``REJECTED`` are immutable terminal states --
the expected projection computation stops updating an intent_id the
moment it reaches one, so a later illegal event can never look like
the truth, only like the ``ILLEGAL_TRANSITION`` it is.

v1.1 adds the same treatment for the worker registry
(``worker_registered``/``worker_registration_rejected``, appended by
``leira.registry.registry.WorkerRegistry``): ``worker_projection`` is
checked against ledger history using the exact same
"recompute-in-memory, compare read-only" pattern as every other
projection. A worker name is immutable once registered, so a second
``worker_registered`` event for the same name (which the live registry
itself cannot have produced in a single process, but which a
restarted process re-registering the same name legitimately can) is
reported as ``DUPLICATE_WORKER_REGISTRATION``, and the expected
projection keeps only the first such event. A
``worker_registration_rejected`` event never produces an expected
projection entry, so a rejected name appearing in the real
``worker_projection`` table is reported as
``WORKER_PROJECTION_UNEXPECTED_ENTRY``.

v1.2 adds the same treatment for the claim store
(``leira.claims.claims.ClaimKernel``): ``intent_claim_projection`` is
checked the same way, reusing
``leira.claims.claims.replay_claim_events`` directly so the "what is
the current active claim" rule is defined exactly once, not
redefined here. A second claim-established event while one is already
active is reported as ``DUPLICATE_ACTIVE_CLAIM``; a release whose
``claim_id``/``owner_id`` doesn't match the active claim is reported
as ``RELEASE_OWNER_MISMATCH``. An orphaned claim (established, never
released) is, by design, not an error: it is exactly the visible,
unrepaired state the claim store's own failure model calls for, and
``check_claim_projection`` reports it as a normal, matching entry.

v1.3 adds the same treatment for receipt bundles
(``leira.receipts.receipts``): ``receipt_projection`` is checked
against an independent, in-memory recomputation of "every ledger event
for this intent_id" -- the same direct-payload-match plus
run_created-operation_id-bridge rule ``list_receipt_events`` applies
live, reimplemented here rather than imported, exactly as every prior
projection check (run/intent/worker) is its own independent
recomputation rather than a call into the live module. A bundle's
``event_count`` mismatching the ledger's own count for that intent_id
is the bundle-completeness check the spec calls for -- it is the same
``RECEIPT_EVENT_COUNT_MISMATCH`` code, not a separate one, since the
expected count is, by construction, every ledger event found for that
intent_id. Receipts introduce no new event types and no new illegal
transitions of their own -- an event appearing after an intent's
terminal state is still exactly what ``check_intent_transitions``
already reports; this section never duplicates that check.

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
from pathlib import Path

from leira.claims.claims import (
    CLAIM_ESTABLISHED_EVENT_TYPE,
    CLAIM_RELEASED_EVENT_TYPE,
    replay_claim_events,
)
from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import ALLOWED_TRANSITIONS
from leira.environment.environment import ENVIRONMENT_EVENT_TYPES
from leira.inbox.inbox import (
    ALLOWED_INTENT_TRANSITIONS,
    INTENT_LEDGER_EVENT_TYPES,
    TERMINAL_INTENT_STATUSES,
)
from leira.projection.state import RUN_LIFECYCLE_EVENT_TYPES
from leira.provenance.git_provenance import PROVENANCE_EVENT_TYPES
from leira.sessions.sessions import SESSION_CREATED_EVENT, SESSION_INTENT_ADDED_EVENT
from leira.workspace.hashing import sha256
from leira.workspace.paths import WorkspaceError, _get_artifact_path
from leira.workspace.workspace import ARTIFACT_FILE_WRITTEN_EVENT

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
    """Artifact events must carry well-formed artifact payloads."""
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
    for event in events:
        if event["event_type"] != ARTIFACT_FILE_WRITTEN_EVENT:
            continue
        payload = _parse_payload(event)
        content = payload.get("content") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("type") != "artifact_file"
            or not isinstance(content, dict)
            or not isinstance(content.get("artifact_id"), str)
            or not content.get("artifact_id")
            or not isinstance(content.get("intent_id"), str)
            or not content.get("intent_id")
            or not isinstance(content.get("relative_path"), str)
            or not content.get("relative_path")
            or not isinstance(content.get("sha256"), str)
            or not content.get("sha256")
            or not isinstance(content.get("size_bytes"), int)
            or content.get("size_bytes") < 0
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


def compute_expected_submission_status(events: list[dict]) -> dict[str, str]:
    """What inbox_entries.status should say: fixed at submission time, forever.

    inbox_entries is an ingress record, not a live execution tracker --
    it is written once by submit_intent() and never touched again, so
    it is only ever compared against the original
    intent_submitted/intent_rejected event, never against later
    execution events.
    """
    expected: dict[str, str] = {}
    for event in events:
        if event["event_type"] not in ("intent_submitted", "intent_rejected"):
            continue
        payload = _parse_payload(event)
        if payload is None:
            continue
        intent_id = payload.get("intent_id")
        status = payload.get("status")
        if not isinstance(intent_id, str) or not intent_id or not isinstance(status, str):
            continue
        expected[intent_id] = status
    return expected


def compute_expected_intents(
    events: list[dict],
) -> dict[str, tuple[str, str, str, str | None]]:
    """Recompute, in memory only, what each intent's intent_projection row should say.

    Walks the full intent lifecycle (submitted/rejected/claimed/
    completed/failed) chronologically. Once an intent_id reaches a
    terminal status (REJECTED/COMPLETED/FAILED), this stops updating
    it -- a terminal state is immutable, so a later event for that
    intent_id (illegal history) is never treated as the truth here,
    only reported elsewhere as ILLEGAL_TRANSITION.
    """
    expected: dict[str, tuple[str, str, str, str | None]] = {}
    terminal_intent_ids: set[str] = set()
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
        if intent_id in terminal_intent_ids:
            continue

        worker_name = payload.get("worker_name")
        worker_name = worker_name if isinstance(worker_name, str) else None
        expected[intent_id] = (status, event["id"], event["created_at"], worker_name)

        if status in TERMINAL_INTENT_STATUSES:
            terminal_intent_ids.add(intent_id)
    return expected


def check_intents(ledger: LedgerKernel, events: list[dict]) -> list[str]:
    """Compare inbox_entries and intent_projection against the ledger.

    Read-only: two SELECTs against inbox_entries and intent_projection.
    If either table doesn't exist, every expected intent is reported
    missing from it -- a disagreement to report, not a reason to
    create the table.
    """
    expected_submission = compute_expected_submission_status(events)
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
            "SELECT intent_id, status, worker_name, last_event_id, updated_at "
            "FROM intent_projection"
        ).fetchall()
    except sqlite3.OperationalError:
        projection_rows = []
    projection = {row[0]: (row[1], row[2], row[3], row[4]) for row in projection_rows}

    errors: list[str] = []

    for intent_id in sorted(expected_submission):
        expected_status = expected_submission[intent_id]
        inbox_status = inbox.get(intent_id)
        if inbox_status is None:
            errors.append(f"MISSING_INBOX_ROW:{intent_id}")
        elif inbox_status != expected_status:
            errors.append(f"INTENT_STATUS_MISMATCH:{intent_id}")

    for intent_id in sorted(expected):
        expected_status, expected_event_id, expected_updated_at, expected_worker_name = expected[
            intent_id
        ]
        projection_entry = projection.get(intent_id)

        if projection_entry is None:
            errors.append(f"INTENT_PROJECTION_MISMATCH:{intent_id}")
            continue

        actual_status, actual_worker_name, actual_event_id, actual_updated_at = projection_entry
        if actual_status != expected_status:
            errors.append(f"INTENT_PROJECTION_MISMATCH:{intent_id}")
        if expected_worker_name is not None and actual_worker_name != expected_worker_name:
            errors.append(f"INTENT_PROJECTION_MISMATCH:{intent_id}")
        if actual_event_id != expected_event_id:
            errors.append(f"INTENT_PROJECTION_LAST_EVENT_ID_MISMATCH:{intent_id}")
        if actual_updated_at != expected_updated_at:
            errors.append(f"INTENT_PROJECTION_UPDATED_AT_MISMATCH:{intent_id}")

    return errors


def check_intent_transitions(events: list[dict]) -> list[str]:
    """Replay each intent's events and verify every transition is legal.

    Reuses leira.inbox.inbox.ALLOWED_INTENT_TRANSITIONS directly,
    exactly the way check_lifecycle_transitions reuses
    ALLOWED_TRANSITIONS for runs. The first event for any intent_id
    must be intent_submitted or intent_rejected; anything else as a
    first event is already illegal.
    """
    errors: list[str] = []
    last_event_type_by_intent: dict[str, str] = {}
    for event in events:
        event_type = event["event_type"]
        if event_type not in INTENT_LEDGER_EVENT_TYPES:
            continue
        payload = _parse_payload(event)
        if payload is None:
            continue
        intent_id = payload.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            continue

        previous_event_type = last_event_type_by_intent.get(intent_id)
        if previous_event_type is None:
            if event_type not in ("intent_submitted", "intent_rejected"):
                errors.append(f"ILLEGAL_TRANSITION:{event['id']}")
        elif event_type not in ALLOWED_INTENT_TRANSITIONS.get(previous_event_type, []):
            errors.append(f"ILLEGAL_TRANSITION:{event['id']}")

        last_event_type_by_intent[intent_id] = event_type
    return errors


def check_duplicate_claims(events: list[dict]) -> list[str]:
    """No intent_id may be claimed (intent_claimed) more than once."""
    errors: list[str] = []
    seen: set[str] = set()
    for event in events:
        if event["event_type"] != "intent_claimed":
            continue
        payload = _parse_payload(event)
        if payload is None:
            continue
        intent_id = payload.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            continue
        if intent_id in seen:
            errors.append(f"DUPLICATE_CLAIM:{intent_id}")
        seen.add(intent_id)
    return errors


def check_worker_name_consistency(events: list[dict]) -> list[str]:
    """worker_name on intent_completed/intent_failed must match the claim's.

    worker_name is provenance, not routing -- but provenance that
    silently changed mid-dispatch would be exactly the kind of quiet
    drift an auditor exists to catch.
    """
    errors: list[str] = []
    claimed_worker_name: dict[str, str] = {}
    for event in events:
        event_type = event["event_type"]
        if event_type not in ("intent_claimed", "intent_completed", "intent_failed"):
            continue
        payload = _parse_payload(event)
        if payload is None:
            continue
        intent_id = payload.get("intent_id")
        worker_name = payload.get("worker_name")
        if not isinstance(intent_id, str) or not intent_id:
            continue

        if event_type == "intent_claimed":
            if isinstance(worker_name, str):
                claimed_worker_name[intent_id] = worker_name
            continue

        expected_worker_name = claimed_worker_name.get(intent_id)
        if expected_worker_name is not None and worker_name != expected_worker_name:
            errors.append(f"WORKER_NAME_MISMATCH:{event['id']}")
    return errors


def compute_expected_worker_projection(events: list[dict]) -> dict[str, tuple[str, str]]:
    """Recompute, in memory only, what worker_projection should say.

    Only worker_registered events ever produce an entry --
    worker_registration_rejected never does, by construction, so a
    rejected name can never look like a registered one here. A worker
    name is immutable once registered: a second worker_registered
    event for the same name (illegal history -- see
    check_duplicate_worker_registrations) is ignored, never treated as
    an update.
    """
    expected: dict[str, tuple[str, str]] = {}
    for event in events:
        if event["event_type"] != "worker_registered":
            continue
        payload = _parse_payload(event)
        if payload is None:
            continue
        worker_name = payload.get("worker_name")
        if not isinstance(worker_name, str) or not worker_name:
            continue
        if worker_name in expected:
            continue
        expected[worker_name] = (event["created_at"], event["id"])
    return expected


def check_duplicate_worker_registrations(events: list[dict]) -> list[str]:
    """No worker_name may have more than one worker_registered event.

    The live registry can never produce this within a single process
    (it checks its own in-memory dict before appending), but a
    restarted process re-registering the same name legitimately can --
    this is the auditor's independent, ledger-wide safety net.
    """
    errors: list[str] = []
    seen: set[str] = set()
    for event in events:
        if event["event_type"] != "worker_registered":
            continue
        payload = _parse_payload(event)
        if payload is None:
            continue
        worker_name = payload.get("worker_name")
        if not isinstance(worker_name, str) or not worker_name:
            continue
        if worker_name in seen:
            errors.append(f"DUPLICATE_WORKER_REGISTRATION:{worker_name}")
        seen.add(worker_name)
    return errors


def check_worker_projection(ledger: LedgerKernel, events: list[dict]) -> list[str]:
    """Compare the real worker_projection table against the in-memory expected one.

    Read-only: a single SELECT against worker_projection. A name
    present in the real table but absent from the expected mapping
    (e.g. a rejected registration that was somehow recorded as
    registered) is reported too -- rejected registrations must never
    appear as registered workers.
    """
    expected = compute_expected_worker_projection(events)

    try:
        actual_rows = ledger.connection.execute(
            "SELECT worker_name, registered_at, last_event_id FROM worker_projection"
        ).fetchall()
    except sqlite3.OperationalError:
        actual_rows = []
    actual = {row[0]: (row[1], row[2]) for row in actual_rows}

    errors: list[str] = []
    for worker_name in sorted(expected):
        expected_registered_at, expected_event_id = expected[worker_name]
        actual_entry = actual.get(worker_name)

        if actual_entry is None:
            errors.append(f"WORKER_PROJECTION_MISMATCH:{worker_name}")
            continue

        actual_registered_at, actual_event_id = actual_entry
        if actual_registered_at != expected_registered_at:
            errors.append(f"WORKER_PROJECTION_UPDATED_AT_MISMATCH:{worker_name}")
        if actual_event_id != expected_event_id:
            errors.append(f"WORKER_PROJECTION_LAST_EVENT_ID_MISMATCH:{worker_name}")

    for worker_name in sorted(set(actual) - set(expected)):
        errors.append(f"WORKER_PROJECTION_UNEXPECTED_ENTRY:{worker_name}")

    return errors


def _group_claim_events_by_intent(events: list[dict]) -> dict[str, list[tuple[str, dict, str, str]]]:
    events_by_intent: dict[str, list[tuple[str, dict, str, str]]] = {}
    for event in events:
        if event["event_type"] not in (CLAIM_ESTABLISHED_EVENT_TYPE, CLAIM_RELEASED_EVENT_TYPE):
            continue
        payload = _parse_payload(event)
        if payload is None:
            continue
        intent_id = payload.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            continue
        events_by_intent.setdefault(intent_id, []).append(
            (event["event_type"], payload, event["created_at"], event["id"])
        )
    return events_by_intent


def check_claim_history(events: list[dict]) -> list[str]:
    """Replay each intent's claim/release events and flag illegal moments.

    A second CLAIM_ESTABLISHED_EVENT_TYPE while one is already active
    is DUPLICATE_ACTIVE_CLAIM. A release whose claim_id or owner_id
    does not match the active claim is RELEASE_OWNER_MISMATCH. Neither
    is silently absorbed by replay_claim_events (which just ignores
    them to compute final state) -- this is where they are reported.
    """
    errors: list[str] = []
    for intent_events in _group_claim_events_by_intent(events).values():
        active: tuple[str, str] | None = None  # (claim_id, owner_id)
        for event_type, payload, _created_at, event_id in intent_events:
            if event_type == CLAIM_ESTABLISHED_EVENT_TYPE:
                if active is not None:
                    errors.append(f"DUPLICATE_ACTIVE_CLAIM:{event_id}")
                    continue
                claim_id = payload.get("claim_id")
                owner_id = payload.get("owner_id")
                if isinstance(claim_id, str) and claim_id and isinstance(owner_id, str) and owner_id:
                    active = (claim_id, owner_id)
            else:
                if active is None:
                    continue
                claim_id = payload.get("claim_id")
                owner_id = payload.get("owner_id")
                if (claim_id, owner_id) == active:
                    active = None
                else:
                    errors.append(f"RELEASE_OWNER_MISMATCH:{event_id}")
    return errors


def compute_expected_claim_projection(events: list[dict]) -> dict[str, tuple[str, str, str, str]]:
    """Recompute, in memory only, what intent_claim_projection should say.

    Reuses leira.claims.claims.replay_claim_events directly -- the
    same rule the live claim store applies -- grouped by intent_id, in
    ledger insertion order.
    """
    expected: dict[str, tuple[str, str, str, str]] = {}
    for intent_id, intent_events in _group_claim_events_by_intent(events).items():
        active = replay_claim_events(intent_events)
        if active is None:
            continue
        expected[intent_id] = (
            active.claim_id,
            active.owner_id,
            active.claimed_at,
            active.last_event_id,
        )
    return expected


def check_claim_projection(ledger: LedgerKernel, events: list[dict]) -> list[str]:
    """Compare the real intent_claim_projection table against the in-memory expected one.

    Read-only: a single SELECT against intent_claim_projection. An
    orphaned claim (established, never released) is, by design, not
    an error here -- it is exactly the visible, unrepaired state this
    check is meant to confirm, not flag.
    """
    expected = compute_expected_claim_projection(events)

    try:
        actual_rows = ledger.connection.execute(
            "SELECT intent_id, claim_id, owner_id, claimed_at, last_event_id "
            "FROM intent_claim_projection"
        ).fetchall()
    except sqlite3.OperationalError:
        actual_rows = []
    actual = {row[0]: (row[1], row[2], row[3], row[4]) for row in actual_rows}

    errors: list[str] = []
    for intent_id in sorted(expected):
        expected_claim_id, expected_owner_id, expected_claimed_at, expected_event_id = expected[intent_id]
        actual_entry = actual.get(intent_id)

        if actual_entry is None:
            errors.append(f"CLAIM_PROJECTION_MISMATCH:{intent_id}")
            continue

        actual_claim_id, actual_owner_id, actual_claimed_at, actual_event_id = actual_entry
        if actual_claim_id != expected_claim_id or actual_owner_id != expected_owner_id:
            errors.append(f"CLAIM_PROJECTION_MISMATCH:{intent_id}")
        if actual_claimed_at != expected_claimed_at:
            errors.append(f"CLAIM_PROJECTION_UPDATED_AT_MISMATCH:{intent_id}")
        if actual_event_id != expected_event_id:
            errors.append(f"CLAIM_PROJECTION_LAST_EVENT_ID_MISMATCH:{intent_id}")

    for intent_id in sorted(set(actual) - set(expected)):
        errors.append(f"CLAIM_PROJECTION_UNEXPECTED_ENTRY:{intent_id}")

    return errors


def compute_expected_receipt_projection(events: list[dict]) -> dict[str, tuple[str, str, int, str]]:
    """Recompute, in memory only, what receipt_projection should say.

    Independent reimplementation of leira.receipts.receipts'
    "which events belong to this intent_id" rule -- direct intent_id
    payload matches, plus run_created rows whose operation_id column
    is this intent_id, plus every event referencing the run_id such a
    run_created row produced. Reimplemented here rather than imported,
    exactly like every other projection check in this module: the
    auditor's expected state is never computed by calling into the
    live module it is checking.
    """
    direct_intent_id_by_event_id: dict[str, str] = {}
    run_id_by_intent_id: dict[str, str] = {}

    for event in events:
        payload = _parse_payload(event)
        if payload is None:
            continue
        intent_id = payload.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            content = payload.get("content")
            if isinstance(content, dict):
                intent_id = content.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            artifact = payload.get("artifact")
            content = artifact.get("content") if isinstance(artifact, dict) else None
            if isinstance(content, dict):
                intent_id = content.get("intent_id")
        if isinstance(intent_id, str) and intent_id:
            direct_intent_id_by_event_id[event["id"]] = intent_id

        if event["event_type"] == "run_created":
            operation_id = event.get("operation_id")
            run_id = payload.get("run_id")
            if (
                isinstance(operation_id, str)
                and operation_id
                and isinstance(run_id, str)
                and run_id
            ):
                run_id_by_intent_id[run_id] = operation_id

    events_by_intent: dict[str, list[dict]] = {}
    for event in events:
        intent_id = direct_intent_id_by_event_id.get(event["id"])
        if intent_id is None:
            payload = _parse_payload(event)
            run_id = payload.get("run_id") if payload else None
            if isinstance(run_id, str) and run_id:
                intent_id = run_id_by_intent_id.get(run_id)
        if intent_id is None:
            continue
        events_by_intent.setdefault(intent_id, []).append(event)

    expected: dict[str, tuple[str, str, int, str]] = {}
    for intent_id, intent_events in events_by_intent.items():
        expected[intent_id] = (
            intent_events[0]["id"],
            intent_events[-1]["id"],
            len(intent_events),
            intent_events[-1]["created_at"],
        )
    return expected


def check_receipt_projection(ledger: LedgerKernel, events: list[dict]) -> list[str]:
    """Compare existing receipt_projection rows against the in-memory expected ones.

    Unlike every other projection in this system, receipt_projection
    is never eagerly kept live by some other module's write path --
    a bundle is only ever materialized when something explicitly calls
    get_receipt_bundle()/rebuild_receipt_projection() for that
    intent_id. So this check is one-directional: it walks the rows
    that actually exist in receipt_projection and verifies each one
    against the ledger, rather than requiring a row for every intent
    the ledger has ever seen. RECEIPT_EVENT_COUNT_MISMATCH doubles as
    the bundle-completeness check the spec calls for: the expected
    count is, by construction, every ledger event found for that
    intent_id, so a mismatch means either a ghost row or a missing
    one, for any intent_id that does have a projection row.
    """
    expected = compute_expected_receipt_projection(events)

    try:
        actual_rows = ledger.connection.execute(
            "SELECT intent_id, first_event_id, last_event_id, event_count, updated_at "
            "FROM receipt_projection"
        ).fetchall()
    except sqlite3.OperationalError:
        actual_rows = []

    errors: list[str] = []
    for intent_id, actual_first, actual_last, actual_count, actual_updated_at in sorted(
        actual_rows, key=lambda row: row[0]
    ):
        expected_entry = expected.get(intent_id)
        if expected_entry is None:
            errors.append(f"RECEIPT_PROJECTION_UNEXPECTED_ENTRY:{intent_id}")
            continue

        expected_first, expected_last, expected_count, expected_updated_at = expected_entry
        if actual_first != expected_first:
            errors.append(f"RECEIPT_FIRST_EVENT_ID_MISMATCH:{intent_id}")
        if actual_last != expected_last:
            errors.append(f"RECEIPT_LAST_EVENT_ID_MISMATCH:{intent_id}")
        if actual_count != expected_count:
            errors.append(f"RECEIPT_EVENT_COUNT_MISMATCH:{intent_id}")
        if actual_updated_at != expected_updated_at:
            errors.append(f"RECEIPT_UPDATED_AT_MISMATCH:{intent_id}")

    return errors


def compute_expected_artifact_projection(events: list[dict]) -> dict[str, tuple[str, str, str, int, str, str]]:
    """Recompute, in memory only, what artifact_projection should say."""
    expected: dict[str, tuple[str, str, str, int, str, str]] = {}
    for event in events:
        if event["event_type"] != ARTIFACT_FILE_WRITTEN_EVENT:
            continue
        payload = _parse_payload(event)
        content = payload.get("content") if isinstance(payload, dict) else None
        if not isinstance(content, dict):
            continue
        artifact_id = content.get("artifact_id")
        intent_id = content.get("intent_id")
        relative_path = content.get("relative_path")
        digest = content.get("sha256")
        size_bytes = content.get("size_bytes")
        if (
            not isinstance(artifact_id, str)
            or not artifact_id
            or not isinstance(intent_id, str)
            or not intent_id
            or not isinstance(relative_path, str)
            or not relative_path
            or not isinstance(digest, str)
            or not digest
            or not isinstance(size_bytes, int)
            or size_bytes < 0
        ):
            continue
        expected[artifact_id] = (
            intent_id,
            relative_path,
            digest,
            size_bytes,
            event["created_at"],
            event["id"],
        )
    return expected


def check_artifact_projection(ledger: LedgerKernel, events: list[dict]) -> list[str]:
    expected = compute_expected_artifact_projection(events)
    try:
        actual_rows = ledger.connection.execute(
            "SELECT artifact_id, intent_id, relative_path, sha256, size_bytes, created_at, last_event_id "
            "FROM artifact_projection"
        ).fetchall()
    except sqlite3.OperationalError:
        actual_rows = []
    actual = {row[0]: (row[1], row[2], row[3], row[4], row[5], row[6]) for row in actual_rows}

    errors: list[str] = []
    for artifact_id in sorted(expected):
        expected_entry = expected[artifact_id]
        actual_entry = actual.get(artifact_id)
        if actual_entry is None:
            errors.append(f"ARTIFACT_PROJECTION_MISMATCH:{artifact_id}")
            continue
        if actual_entry[:5] != expected_entry[:5]:
            errors.append(f"ARTIFACT_PROJECTION_MISMATCH:{artifact_id}")
        if actual_entry[5] != expected_entry[5]:
            errors.append(f"ARTIFACT_PROJECTION_LAST_EVENT_ID_MISMATCH:{artifact_id}")

    for artifact_id in sorted(set(actual) - set(expected)):
        errors.append(f"ARTIFACT_PROJECTION_UNEXPECTED_ENTRY:{artifact_id}")
    return errors


def check_artifact_files(
    workspace_root: str | Path | None, events: list[dict]
) -> list[str]:
    if workspace_root is None:
        return []

    errors: list[str] = []
    root = Path(workspace_root)
    for artifact_id, (
        intent_id,
        relative_path,
        digest,
        size_bytes,
        _created_at,
        _event_id,
    ) in sorted(compute_expected_artifact_projection(events).items()):
        try:
            path = _get_artifact_path(root, intent_id, relative_path)
        except WorkspaceError:
            errors.append(f"ARTIFACT_PATH_INVALID:{artifact_id}")
            continue
        if not path.exists() or not path.is_file():
            errors.append(f"MISSING_ARTIFACT_FILE:{artifact_id}")
            continue
        content = path.read_bytes()
        if len(content) != size_bytes:
            errors.append(f"SIZE_MISMATCH:{artifact_id}")
            continue
        if sha256(content) != digest:
            errors.append(f"HASH_MISMATCH:{artifact_id}")
    return errors


def _parse_provenance_content(event: dict) -> dict | None:
    if event["event_type"] not in PROVENANCE_EVENT_TYPES:
        return None
    payload = _parse_payload(event)
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("type") != "provenance":
        return None
    return content if isinstance(content, dict) else None


def check_provenance_schema(events: list[dict]) -> list[str]:
    errors: list[str] = []
    for event in events:
        if event["event_type"] not in PROVENANCE_EVENT_TYPES:
            continue
        content = _parse_provenance_content(event)
        if (
            not isinstance(content, dict)
            or not isinstance(content.get("snapshot_id"), str)
            or not content.get("snapshot_id")
            or not isinstance(content.get("intent_id"), str)
            or not content.get("intent_id")
            or not isinstance(content.get("repo_path"), str)
            or not isinstance(content.get("status_porcelain"), str)
            or not isinstance(content.get("stderr", ""), str)
            or (
                content.get("is_dirty") is not None
                and not isinstance(content.get("is_dirty"), bool)
            )
        ):
            errors.append(f"PROVENANCE_SCHEMA_INVALID:{event['id']}")
            continue
        if event["event_type"] == "provenance_capture_failed" and not isinstance(
            content.get("error_type"), str
        ):
            errors.append(f"PROVENANCE_FAILURE_ERROR_TYPE_MISSING:{event['id']}")
        if event["event_type"] == "provenance_captured" and content.get("error_type") is not None:
            errors.append(f"PROVENANCE_CAPTURE_ERROR_UNEXPECTED:{event['id']}")
    return errors


def check_unique_provenance_snapshot_ids(events: list[dict]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for event in events:
        content = _parse_provenance_content(event)
        if content is None:
            continue
        snapshot_id = content.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            continue
        if snapshot_id in seen:
            errors.append(f"DUPLICATE_PROVENANCE_SNAPSHOT_ID:{snapshot_id}")
        seen.add(snapshot_id)
    return errors


def compute_expected_provenance_projection(
    events: list[dict],
) -> dict[str, tuple[str, str, str | None, str | None, bool | None, str, str, str | None, str, str]]:
    expected: dict[
        str,
        tuple[str, str, str | None, str | None, bool | None, str, str, str | None, str, str],
    ] = {}
    for event in events:
        content = _parse_provenance_content(event)
        if content is None:
            continue
        snapshot_id = content.get("snapshot_id")
        intent_id = content.get("intent_id")
        repo_path = content.get("repo_path")
        status_porcelain = content.get("status_porcelain")
        stderr = content.get("stderr", "")
        is_dirty = content.get("is_dirty")
        if (
            not isinstance(snapshot_id, str)
            or not snapshot_id
            or not isinstance(intent_id, str)
            or not intent_id
            or not isinstance(repo_path, str)
            or not isinstance(status_porcelain, str)
            or not isinstance(stderr, str)
            or (is_dirty is not None and not isinstance(is_dirty, bool))
        ):
            continue
        expected[snapshot_id] = (
            intent_id,
            repo_path,
            content.get("head_sha"),
            content.get("branch"),
            is_dirty,
            status_porcelain,
            event["created_at"],
            content.get("error_type"),
            stderr,
            event["id"],
        )
    return expected


def check_provenance_projection(ledger: LedgerKernel, events: list[dict]) -> list[str]:
    expected = compute_expected_provenance_projection(events)
    try:
        actual_rows = ledger.connection.execute(
            "SELECT snapshot_id, intent_id, repo_path, head_sha, branch, is_dirty, "
            "status_porcelain, created_at, error_type, stderr, last_event_id "
            "FROM provenance_projection"
        ).fetchall()
    except sqlite3.OperationalError:
        actual_rows = []
    actual = {
        row[0]: (
            row[1],
            row[2],
            row[3],
            row[4],
            None if row[5] is None else bool(row[5]),
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
        )
        for row in actual_rows
    }

    errors: list[str] = []
    for snapshot_id in sorted(expected):
        expected_entry = expected[snapshot_id]
        actual_entry = actual.get(snapshot_id)
        if actual_entry is None:
            errors.append(f"PROVENANCE_PROJECTION_MISMATCH:{snapshot_id}")
            continue
        if actual_entry[:9] != expected_entry[:9]:
            errors.append(f"PROVENANCE_PROJECTION_MISMATCH:{snapshot_id}")
        if actual_entry[9] != expected_entry[9]:
            errors.append(f"PROVENANCE_PROJECTION_LAST_EVENT_ID_MISMATCH:{snapshot_id}")

    for snapshot_id in sorted(set(actual) - set(expected)):
        errors.append(f"PROVENANCE_PROJECTION_UNEXPECTED_ENTRY:{snapshot_id}")
    return errors


def _parse_environment_content(event: dict) -> dict | None:
    if event["event_type"] not in ENVIRONMENT_EVENT_TYPES:
        return None
    payload = _parse_payload(event)
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("type") != "environment":
        return None
    return content if isinstance(content, dict) else None


def check_environment_schema(events: list[dict]) -> list[str]:
    errors: list[str] = []
    for event in events:
        content = _parse_environment_content(event)
        if content is None:
            continue
        packages = content.get("installed_packages")
        if (
            not isinstance(content.get("snapshot_id"), str)
            or not content.get("snapshot_id")
            or not isinstance(content.get("intent_id"), str)
            or not content.get("intent_id")
            or not isinstance(content.get("python_version"), str)
            or not isinstance(content.get("platform"), str)
            or not isinstance(content.get("executable"), str)
            or not isinstance(packages, list)
            or not isinstance(content.get("stderr", ""), str)
        ):
            errors.append(f"ENVIRONMENT_SCHEMA_INVALID:{event['id']}")
            continue
        normalized = []
        for package in packages:
            if (
                not isinstance(package, dict)
                or not isinstance(package.get("name"), str)
                or not isinstance(package.get("version"), str)
            ):
                errors.append(f"ENVIRONMENT_SCHEMA_INVALID:{event['id']}")
                break
            normalized.append((package["name"].lower(), package["version"]))
        else:
            if normalized != sorted(normalized):
                errors.append(f"ENVIRONMENT_PACKAGES_NOT_SORTED:{event['id']}")
        if event["event_type"] == "environment_capture_failed" and not isinstance(
            content.get("error_type"), str
        ):
            errors.append(f"ENVIRONMENT_FAILURE_ERROR_TYPE_MISSING:{event['id']}")
        if event["event_type"] == "environment_captured" and content.get("error_type") is not None:
            errors.append(f"ENVIRONMENT_CAPTURE_ERROR_UNEXPECTED:{event['id']}")
    return errors


def check_unique_environment_snapshot_ids(events: list[dict]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for event in events:
        content = _parse_environment_content(event)
        if content is None:
            continue
        snapshot_id = content.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            continue
        if snapshot_id in seen:
            errors.append(f"DUPLICATE_ENVIRONMENT_SNAPSHOT_ID:{snapshot_id}")
        seen.add(snapshot_id)
    return errors


def compute_expected_environment_projection(
    events: list[dict],
) -> dict[str, tuple[str, str, str, str, str, str | None, str]]:
    expected: dict[str, tuple[str, str, str, str, str, str | None, str]] = {}
    for event in events:
        content = _parse_environment_content(event)
        if content is None:
            continue
        snapshot_id = content.get("snapshot_id")
        intent_id = content.get("intent_id")
        python_version = content.get("python_version")
        platform_value = content.get("platform")
        executable = content.get("executable")
        if (
            not isinstance(snapshot_id, str)
            or not snapshot_id
            or not isinstance(intent_id, str)
            or not intent_id
            or not isinstance(python_version, str)
            or not isinstance(platform_value, str)
            or not isinstance(executable, str)
        ):
            continue
        expected[snapshot_id] = (
            intent_id,
            python_version,
            platform_value,
            executable,
            event["created_at"],
            content.get("error_type"),
            event["id"],
        )
    return expected


def check_environment_projection(ledger: LedgerKernel, events: list[dict]) -> list[str]:
    expected = compute_expected_environment_projection(events)
    try:
        actual_rows = ledger.connection.execute(
            "SELECT snapshot_id, intent_id, python_version, platform, executable, "
            "created_at, error_type, last_event_id FROM environment_projection"
        ).fetchall()
    except sqlite3.OperationalError:
        actual_rows = []
    actual = {row[0]: (row[1], row[2], row[3], row[4], row[5], row[6], row[7]) for row in actual_rows}

    errors: list[str] = []
    for snapshot_id in sorted(expected):
        actual_entry = actual.get(snapshot_id)
        if actual_entry is None:
            errors.append(f"ENVIRONMENT_PROJECTION_MISMATCH:{snapshot_id}")
            continue
        if actual_entry[:6] != expected[snapshot_id][:6]:
            errors.append(f"ENVIRONMENT_PROJECTION_MISMATCH:{snapshot_id}")
        if actual_entry[6] != expected[snapshot_id][6]:
            errors.append(f"ENVIRONMENT_PROJECTION_LAST_EVENT_ID_MISMATCH:{snapshot_id}")
    for snapshot_id in sorted(set(actual) - set(expected)):
        errors.append(f"ENVIRONMENT_PROJECTION_UNEXPECTED_ENTRY:{snapshot_id}")
    return errors


def _session_payload_ids(event: dict) -> tuple[str | None, str | None]:
    payload = _parse_payload(event)
    if payload is None:
        return None, None
    artifact = payload.get("artifact")
    content = artifact.get("content") if isinstance(artifact, dict) else None
    if isinstance(content, dict):
        session_id = content.get("session_id")
        intent_id = content.get("intent_id")
    else:
        session_id = payload.get("session_id")
        intent_id = payload.get("intent_id")
    session_id = session_id if isinstance(session_id, str) and session_id else None
    intent_id = intent_id if isinstance(intent_id, str) and intent_id else None
    return session_id, intent_id


def check_session_history(events: list[dict]) -> list[str]:
    errors: list[str] = []
    known_intents = {
        intent_id
        for event in events
        if event["event_type"] in ("intent_submitted", "intent_rejected")
        for payload in (_parse_payload(event),)
        if isinstance(payload, dict)
        for intent_id in (payload.get("intent_id"),)
        if isinstance(intent_id, str) and intent_id
    }
    known_sessions: set[str] = set()
    seen_memberships: set[tuple[str, str]] = set()
    for event in events:
        if event["event_type"] == SESSION_CREATED_EVENT:
            session_id, _intent_id = _session_payload_ids(event)
            if session_id is not None:
                known_sessions.add(session_id)
            continue
        if event["event_type"] != SESSION_INTENT_ADDED_EVENT:
            continue
        session_id, intent_id = _session_payload_ids(event)
        if session_id is None or intent_id is None:
            errors.append(f"SESSION_MEMBERSHIP_SCHEMA_INVALID:{event['id']}")
            continue
        if session_id not in known_sessions:
            errors.append(f"SESSION_UNKNOWN_SESSION:{event['id']}")
        if intent_id not in known_intents:
            errors.append(f"SESSION_UNKNOWN_INTENT:{event['id']}")
        key = (session_id, intent_id)
        if key in seen_memberships:
            errors.append(f"DUPLICATE_SESSION_MEMBERSHIP:{session_id}:{intent_id}")
        seen_memberships.add(key)
    return errors


def compute_expected_session_projection(
    events: list[dict],
) -> tuple[dict[str, tuple[int, str, str]], dict[tuple[str, str], tuple[int, str, str]]]:
    sessions: dict[str, tuple[int, str, str]] = {}
    membership_rows: dict[tuple[str, str], tuple[int, str, str]] = {}
    counts: dict[str, int] = {}
    seen_memberships: set[tuple[str, str]] = set()
    for event in events:
        if event["event_type"] == SESSION_CREATED_EVENT:
            session_id, _intent_id = _session_payload_ids(event)
            if session_id is None or session_id in sessions:
                continue
            sessions[session_id] = (0, event["created_at"], event["id"])
            counts[session_id] = 0
            continue
        if event["event_type"] != SESSION_INTENT_ADDED_EVENT:
            continue
        session_id, intent_id = _session_payload_ids(event)
        if session_id is None or intent_id is None or session_id not in sessions:
            continue
        key = (session_id, intent_id)
        if key in seen_memberships:
            continue
        seen_memberships.add(key)
        order = counts.get(session_id, 0) + 1
        counts[session_id] = order
        membership_rows[key] = (order, event["created_at"], event["id"])
        _old_count, created_at, _old_event_id = sessions[session_id]
        sessions[session_id] = (order, created_at, event["id"])
    return sessions, membership_rows


def check_session_projection(ledger: LedgerKernel, events: list[dict]) -> list[str]:
    expected_sessions, expected_memberships = compute_expected_session_projection(events)
    try:
        actual_session_rows = ledger.connection.execute(
            "SELECT session_id, intent_count, created_at, last_event_id FROM session_projection"
        ).fetchall()
    except sqlite3.OperationalError:
        actual_session_rows = []
    try:
        actual_membership_rows = ledger.connection.execute(
            "SELECT session_id, intent_id, membership_order, created_at, last_event_id "
            "FROM session_membership_projection"
        ).fetchall()
    except sqlite3.OperationalError:
        actual_membership_rows = []

    actual_sessions = {row[0]: (row[1], row[2], row[3]) for row in actual_session_rows}
    actual_memberships = {
        (row[0], row[1]): (row[2], row[3], row[4]) for row in actual_membership_rows
    }

    errors: list[str] = []
    for session_id in sorted(expected_sessions):
        if actual_sessions.get(session_id) != expected_sessions[session_id]:
            errors.append(f"SESSION_PROJECTION_MISMATCH:{session_id}")
    for session_id in sorted(set(actual_sessions) - set(expected_sessions)):
        errors.append(f"SESSION_PROJECTION_UNEXPECTED_ENTRY:{session_id}")
    for key in sorted(expected_memberships):
        if actual_memberships.get(key) != expected_memberships[key]:
            errors.append(f"SESSION_MEMBERSHIP_PROJECTION_MISMATCH:{key[0]}:{key[1]}")
    for session_id, intent_id in sorted(set(actual_memberships) - set(expected_memberships)):
        errors.append(f"SESSION_MEMBERSHIP_PROJECTION_UNEXPECTED_ENTRY:{session_id}:{intent_id}")
    return errors


def audit(ledger: LedgerKernel, workspace_root: str | Path | None = None) -> AuditResult:
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
    errors.extend(check_intent_transitions(events))
    errors.extend(check_duplicate_claims(events))
    errors.extend(check_worker_name_consistency(events))
    errors.extend(check_duplicate_worker_registrations(events))
    errors.extend(check_claim_history(events))
    errors.extend(check_provenance_schema(events))
    errors.extend(check_unique_provenance_snapshot_ids(events))
    errors.extend(check_environment_schema(events))
    errors.extend(check_unique_environment_snapshot_ids(events))
    errors.extend(check_session_history(events))

    projection_errors = check_projections(ledger, events)
    errors.extend(projection_errors)

    intent_errors = check_intents(ledger, events)
    errors.extend(intent_errors)

    worker_projection_errors = check_worker_projection(ledger, events)
    errors.extend(worker_projection_errors)

    claim_projection_errors = check_claim_projection(ledger, events)
    errors.extend(claim_projection_errors)

    receipt_projection_errors = check_receipt_projection(ledger, events)
    errors.extend(receipt_projection_errors)

    artifact_projection_errors = check_artifact_projection(ledger, events)
    errors.extend(artifact_projection_errors)

    artifact_file_errors = check_artifact_files(workspace_root, events)
    errors.extend(artifact_file_errors)

    provenance_projection_errors = check_provenance_projection(ledger, events)
    errors.extend(provenance_projection_errors)

    environment_projection_errors = check_environment_projection(ledger, events)
    errors.extend(environment_projection_errors)

    session_projection_errors = check_session_projection(ledger, events)
    errors.extend(session_projection_errors)

    projections_valid = (
        len(projection_errors) == 0
        and len(intent_errors) == 0
        and len(worker_projection_errors) == 0
        and len(claim_projection_errors) == 0
        and len(receipt_projection_errors) == 0
        and len(artifact_projection_errors) == 0
        and len(provenance_projection_errors) == 0
        and len(environment_projection_errors) == 0
        and len(session_projection_errors) == 0
    )

    return AuditResult(
        success=len(errors) == 0,
        chain_valid=chain_valid,
        projections_valid=projections_valid,
        errors=errors,
    )
