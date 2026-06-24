"""Leira v0.9 inbox: intent enters, execution waits.

What this is
-------------
``submit_intent()`` is the one door through which a request for work
enters the workshop. It durably records *that* work was requested and
*exactly what* was requested -- nothing more. It never executes
anything, never schedules anything, never decides what should happen
next. A submitted intent sits as ``PENDING``; nothing in this module
ever moves it to any other state. There is no ``RUNNING``, no
``COMPLETED``, no ``FAILED`` here -- those belong to execution, which
is explicitly someone else's problem, later.

Three layers, kept separate on purpose
------------------------------------------
  - Ingress (``inbox_entries``): a durable record that a request
    arrived, in this module's own table.
  - Authority (``ledger_events``): the same request, also recorded as
    an ``intent_submitted`` or ``intent_rejected`` ledger event --
    truth, exactly as for every other event type in this system.
  - Representation (``intent_projection``): a disposable, rebuildable
    one-row-per-intent cache of ``status``/``updated_at``/``last_event_id``,
    derived entirely from the ledger -- recomputed by
    ``rebuild_intent_projection()``, never trusted over the ledger.

The same ``intent_id`` appears in all three. The ledger event is
written first, through the existing, unmodified ``LedgerKernel.append_event``
(no redesign of the ledger). The ``inbox_entries`` and
``intent_projection`` rows are then written together in one
transaction. If that second step fails after the ledger event already
succeeded, the ledger record is still the truth -- ``submit_intent()``
still reports a typed failure to the caller (never pretending
acceptance), and ``rebuild_intent_projection()`` can always recompute
``intent_projection`` from the ledger afterward. ``inbox_entries``
itself has no rebuild path in v0.9; per the failure model, inbox
corruption is recoverable *from the ledger where possible*, not
guaranteed in every case -- the ledger is the only layer that must
never be wrong.

Acceptance vs. rejection
---------------------------
Validation is purely structural: is ``intent_type`` a non-empty
string, is ``payload`` a dict, is ``payload`` JSON-safe (reusing
``kernel.canonicalize_payload`` -- the exact same rules the ledger
itself enforces, not a second, divergent definition of "valid JSON").
Nothing about what the intent *means* is ever interpreted. A rejected
intent is not an exception and not a non-event: it is durably recorded
with ``status="REJECTED"`` in all three layers, exactly like an
accepted intent is recorded with ``status="PENDING"``. Both are
honest, permanent facts about what was requested.

What this explicitly does NOT do
-----------------------------------
No execution, no scheduling, no claiming, no queue runner, no
priorities, no routing, no retries, no stale-intent cleanup, no reaper,
no background task of any kind. Pending intents may accumulate
forever; that is not a bug to clean up here.

v1.0 note: execution itself (claiming, running, completing, failing)
is added by leira.dispatcher.dispatcher, not here. This module is
extended only to recognize the resulting event types
(``intent_claimed``/``intent_completed``/``intent_failed``) so that
``intent_projection`` and ``rebuild_intent_projection()`` can reflect
them, and to expose ``get_intent_status()`` as the one authoritative
way to ask the ledger "what is true about this intent right now."
Terminal statuses (``COMPLETED``, ``FAILED``, and ``REJECTED``) are
immutable: once reached, no later event for that intent_id is ever
applied by rebuild, even if one exists in the ledger -- it is reported
by the auditor as an illegal transition, never repaired here.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from leira.dispatcher.kernel import LedgerKernel, PayloadValidationError, canonicalize_payload

# Worker id recorded against inbox-produced ledger events. The inbox
# itself is the producer here, not a worker in the v0.6 sense.
INBOX_WORKER_ID = "kernel"

# Every ledger event type that carries an intent_id and an intent
# status. intent_submitted/intent_rejected are appended by this module
# (submit_intent); intent_claimed/intent_completed/intent_failed are
# appended by leira.dispatcher.dispatcher.dispatch_once -- recognized
# here so projection/rebuild/audit can treat the whole intent lifecycle
# uniformly without this module depending on the dispatcher module.
INTENT_LEDGER_EVENT_TYPES = frozenset(
    {
        "intent_submitted",
        "intent_rejected",
        "intent_claimed",
        "intent_completed",
        "intent_failed",
    }
)

# event_type -> the status it establishes for that intent_id.
INTENT_STATUS_BY_EVENT_TYPE: dict[str, str] = {
    "intent_submitted": "PENDING",
    "intent_rejected": "REJECTED",
    "intent_claimed": "RUNNING",
    "intent_completed": "COMPLETED",
    "intent_failed": "FAILED",
}

# Intent-level state machine, expressed as data exactly like
# leira.dispatcher.lifecycle.ALLOWED_TRANSITIONS for runs: event_type ->
# allowed next event_types. REJECTED/COMPLETED/FAILED are terminal --
# once reached, nothing may follow for that intent_id.
ALLOWED_INTENT_TRANSITIONS: dict[str, list[str]] = {
    "intent_submitted": ["intent_claimed"],
    "intent_rejected": [],
    "intent_claimed": ["intent_completed", "intent_failed"],
    "intent_completed": [],
    "intent_failed": [],
}

TERMINAL_INTENT_STATUSES = frozenset({"REJECTED", "COMPLETED", "FAILED"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_entries (
    intent_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    intent_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intent_projection (
    intent_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    worker_name TEXT,
    updated_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL
);
"""


def ensure_schema(ledger: LedgerKernel) -> None:
    ledger.connection.executescript(_SCHEMA)
    ledger.connection.commit()


@dataclass(frozen=True)
class IntentEnvelope:
    intent_id: str
    created_at: str
    intent_type: str
    payload: dict


@dataclass(frozen=True)
class SubmitIntentResult:
    success: bool
    intent_id: str | None
    status: str | None = None
    error_type: str | None = None


def validate_intent(intent_type, payload) -> str | None:
    """Purely structural validation. Returns an error_type, or None if valid.

    Never interprets intent_type or payload meaning -- only their
    shape: intent_type must be a non-empty string, payload must be a
    dict, and payload must be JSON-safe by the same rules the ledger
    itself enforces (no floats/NaN/Infinity, no non-string keys, no
    unserializable objects).
    """
    if not isinstance(payload, dict):
        return "INVALID_ENVELOPE"
    if not isinstance(intent_type, str) or not intent_type:
        return "INVALID_ENVELOPE"
    try:
        canonicalize_payload(payload)
    except PayloadValidationError:
        return "NON_SERIALIZABLE_PAYLOAD"
    return None


class InboxKernel:
    """Durable intent ingress on top of an existing LedgerKernel."""

    def __init__(self, ledger: LedgerKernel):
        self._ledger = ledger
        ensure_schema(self._ledger)

    def submit_intent(self, intent_type, payload) -> SubmitIntentResult:
        """Durably record a request for work. Never executes it.

        Always assigns a fresh intent_id, whether the intent is
        accepted or rejected -- both outcomes are durable, permanent
        records. Only a genuine storage failure (the ledger append, or
        the inbox_entries/intent_projection write, could not complete)
        returns intent_id=None: nothing was recorded, so there is
        nothing to refer to.
        """
        intent_id = str(uuid.uuid4())
        error_type = validate_intent(intent_type, payload)
        status = "REJECTED" if error_type else "PENDING"
        event_type = "intent_rejected" if error_type else "intent_submitted"

        stored_intent_type = intent_type if isinstance(intent_type, str) else repr(intent_type)
        if error_type == "NON_SERIALIZABLE_PAYLOAD" or not isinstance(payload, dict):
            stored_payload = {"unrepresentable_payload_repr": repr(payload)}
        else:
            stored_payload = payload

        event_payload = {
            "intent_id": intent_id,
            "intent_type": stored_intent_type,
            "payload": stored_payload,
            "status": status,
            "error_type": error_type,
        }

        append_result = self._ledger.append_event(
            event_type=event_type,
            worker_id=INBOX_WORKER_ID,
            payload=event_payload,
        )
        if not append_result.success:
            return SubmitIntentResult(
                success=False, intent_id=None, status=None, error_type="STORAGE_FAILURE"
            )

        payload_json = canonicalize_payload(stored_payload)

        try:
            with self._ledger.connection:
                self._ledger.connection.execute(
                    """
                    INSERT INTO inbox_entries
                        (intent_id, created_at, intent_type, payload_json, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (intent_id, append_result.created_at, stored_intent_type, payload_json, status),
                )
                self._ledger.connection.execute(
                    """
                    INSERT INTO intent_projection
                        (intent_id, status, worker_name, updated_at, last_event_id)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(intent_id) DO UPDATE SET
                        status = excluded.status,
                        worker_name = excluded.worker_name,
                        updated_at = excluded.updated_at,
                        last_event_id = excluded.last_event_id
                    """,
                    (intent_id, status, None, append_result.created_at, append_result.event_id),
                )
        except sqlite3.Error:
            return SubmitIntentResult(
                success=False, intent_id=None, status=None, error_type="STORAGE_FAILURE"
            )

        if error_type:
            return SubmitIntentResult(
                success=False, intent_id=intent_id, status="REJECTED", error_type=error_type
            )
        return SubmitIntentResult(success=True, intent_id=intent_id, status="PENDING")


def rebuild_intent_projection(ledger: LedgerKernel) -> None:
    """Recompute intent_projection entirely from ledger_events, in one transaction.

    Same shape as leira.projection.rebuild.rebuild_projection(), kept
    as a separate function in this module rather than folded into that
    one -- the projection engine itself is not redesigned. All-or-
    nothing: a failure partway through rolls back to the previous
    table state. updated_at is always the ledger event's own
    created_at, never datetime.now().

    Terminal states are immutable: once an intent_id reaches
    REJECTED/COMPLETED/FAILED during this chronological replay, any
    later event for that same intent_id is ignored here -- such an
    event is illegal history (the auditor reports it as
    ILLEGAL_TRANSITION), and rebuild must not silently apply it.
    """
    ensure_schema(ledger)
    conn = ledger.connection

    rows = conn.execute(
        "SELECT id, event_type, payload_json, created_at FROM ledger_events ORDER BY rowid"
    ).fetchall()

    terminal_intent_ids: set[str] = set()

    with conn:
        conn.execute("DELETE FROM intent_projection")
        for event_id, event_type, payload_json, created_at in rows:
            if event_type not in INTENT_LEDGER_EVENT_TYPES:
                continue
            try:
                payload = json.loads(payload_json)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            intent_id = payload.get("intent_id")
            status = payload.get("status")
            if not isinstance(intent_id, str) or not intent_id or not isinstance(status, str):
                continue
            if intent_id in terminal_intent_ids:
                continue

            worker_name = payload.get("worker_name")
            worker_name = worker_name if isinstance(worker_name, str) else None

            conn.execute(
                """
                INSERT INTO intent_projection
                    (intent_id, status, worker_name, updated_at, last_event_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    status = excluded.status,
                    worker_name = excluded.worker_name,
                    updated_at = excluded.updated_at,
                    last_event_id = excluded.last_event_id
                """,
                (intent_id, status, worker_name, created_at, event_id),
            )

            if status in TERMINAL_INTENT_STATUSES:
                terminal_intent_ids.add(intent_id)


def get_intent_status(ledger: LedgerKernel, intent_id: str) -> str | None:
    """Derive intent_id's current status directly from the ledger.

    Mirrors leira.dispatcher.lifecycle.LifecycleKernel.get_run_state():
    the authoritative answer is always read from ledger_events, never
    from intent_projection. Returns None if intent_id was never
    submitted at all.

    Walks events in chronological order and stops at the first
    terminal status reached -- exactly like rebuild_intent_projection().
    A terminal state is immutable, so even if the ledger somehow
    contains an illegal event after one (a bypass, not something this
    API can produce), that later event is never treated as the truth.
    """
    rows = ledger.connection.execute(
        f"""
        SELECT event_type FROM ledger_events
        WHERE event_type IN ({",".join("?" for _ in INTENT_LEDGER_EVENT_TYPES)})
        AND payload_json LIKE ?
        ORDER BY rowid ASC
        """,
        (*INTENT_LEDGER_EVENT_TYPES, f'%"intent_id":"{intent_id}"%'),
    ).fetchall()

    status: str | None = None
    for (event_type,) in rows:
        status = INTENT_STATUS_BY_EVENT_TYPE[event_type]
        if status in TERMINAL_INTENT_STATUSES:
            break
    return status


def update_intent_projection(
    ledger: LedgerKernel,
    *,
    intent_id: str,
    status: str,
    worker_name: str | None,
    last_event_id: str,
    updated_at: str,
) -> bool:
    """Best-effort live upsert of one intent_projection row. Never raises.

    Used by leira.dispatcher.dispatcher.dispatch_once after each
    successful intent_claimed/intent_completed/intent_failed append, so
    intent_projection normally stays fresh -- mirroring how
    LifecycleKernel optionally keeps operation_state_projection fresh.
    A False return (or never being called at all) is not a problem:
    rebuild_intent_projection() can always recompute this table from
    the ledger. A projection write failure must never be treated as a
    reason to undo or distrust the ledger append that already
    succeeded.
    """
    try:
        with ledger.connection:
            ledger.connection.execute(
                """
                INSERT INTO intent_projection
                    (intent_id, status, worker_name, updated_at, last_event_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    status = excluded.status,
                    worker_name = excluded.worker_name,
                    updated_at = excluded.updated_at,
                    last_event_id = excluded.last_event_id
                """,
                (intent_id, status, worker_name, updated_at, last_event_id),
            )
        return True
    except sqlite3.Error:
        return False
