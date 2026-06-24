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

# The only two ledger event types this module ever appends.
INTENT_LEDGER_EVENT_TYPES = frozenset({"intent_submitted", "intent_rejected"})

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
                        (intent_id, status, updated_at, last_event_id)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(intent_id) DO UPDATE SET
                        status = excluded.status,
                        updated_at = excluded.updated_at,
                        last_event_id = excluded.last_event_id
                    """,
                    (intent_id, status, append_result.created_at, append_result.event_id),
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
    """
    ensure_schema(ledger)
    conn = ledger.connection

    rows = conn.execute(
        "SELECT id, event_type, payload_json, created_at FROM ledger_events ORDER BY rowid"
    ).fetchall()

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
            conn.execute(
                """
                INSERT INTO intent_projection
                    (intent_id, status, updated_at, last_event_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    last_event_id = excluded.last_event_id
                """,
                (intent_id, status, created_at, event_id),
            )
