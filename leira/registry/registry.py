"""Leira v1.1 worker registry: the reception desk, not the router.

What this is
-------------
``WorkerRegistry`` is an in-process ``dict[str, Worker]`` -- nothing
more. ``register_worker()`` records that a name now refers to a given
worker object; ``get_worker()`` and ``list_workers()`` answer "what is
registered, right now, in this process" by exact name only. There is
no ranking, no choosing, no inspection of what a worker does or
returns, no instantiation, no module scanning, no dynamic import. A
caller who wants a worker dispatched still has to ask for it by its
exact, case-sensitive name -- the registry resolves a name to an
object; it never decides which name to use.

Registry vs. projection: two different kinds of memory
------------------------------------------------------
The in-memory registry (``self._workers``) cannot be reconstructed
from the ledger. Worker objects are live Python references that exist
only for the lifetime of this process -- replaying ledger events
cannot bring a Python object back into existence. ``worker_projection``
is a different, much smaller thing: a disposable record that *a name*
was registered, at *what time*, by *which ledger event*. Rebuilding
``worker_projection`` from the ledger (see ``rebuild_worker_projection``
below) recreates that record faithfully; it does not, and cannot,
recreate the registered worker object itself. Treat the two as
unrelated guarantees: the registry's truth lives in this process's
memory and is lost when the process exits; the projection's truth
lives in the ledger and survives forever.

Registration-ledger atomicity
------------------------------
Exactly the ordering discipline used everywhere else in this system:
1. validate the worker (a usable, non-empty ``name``, not already
   registered in this process),
2. append the ledger event (``worker_registered`` on success,
   ``worker_registration_rejected`` on validation failure),
3. update the in-memory dict -- only after step 2 succeeds, and only
   for a successful registration.
If the ledger append itself fails, the worker is never stored: there
is no successful in-memory registration without a corresponding
ledger event.

Why a live duplicate check is not enough
-----------------------------------------
``register_worker()`` only rejects a duplicate name against this
process's own current dict -- by design, since the registry is
in-memory only and cannot see history from a previous process. A
restarted process that re-registers a name the ledger already saw
``worker_registered`` for will succeed here (the new dict starts
empty) and produce a second, legitimate-looking ``worker_registered``
event in the ledger for the same name. That is exactly the kind of
drift the auditor exists to catch (see
``leira.audit.auditor.check_duplicate_worker_registrations``) -- this
module reports nothing about ledger history, only about its own
process's memory.

What this explicitly does NOT do
-----------------------------------
No routing, no priorities, no capabilities, no tags, no dependency
injection, no plugin loading, no automatic discovery, no dynamic
imports, no module scanning, no load balancing, no retries, no
persistent worker objects across process restarts, no inference about
what a worker can do from its outputs or payloads.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from leira.dispatcher.kernel import LedgerKernel
from leira.workers.base import Worker

# Worker id recorded against registry-produced ledger events. The
# registry itself is the producer here, exactly as the inbox and
# dispatcher record "kernel" against their own structural events.
REGISTRY_WORKER_ID = "kernel"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_projection (
    worker_name TEXT PRIMARY KEY,
    registered_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL
);
"""


def ensure_schema(ledger: LedgerKernel) -> None:
    ledger.connection.executescript(_SCHEMA)
    ledger.connection.commit()


@dataclass(frozen=True)
class RegisterResult:
    success: bool
    worker_name: str | None
    error_type: str | None = None


class WorkerRegistry:
    """An in-process dict[str, Worker], backed by ledger provenance events."""

    def __init__(self, ledger: LedgerKernel):
        self._ledger = ledger
        self._workers: dict[str, Worker] = {}
        ensure_schema(self._ledger)

    def register_worker(self, worker: Worker) -> RegisterResult:
        """Validate, record to the ledger, then -- only then -- store the reference.

        Stores the worker object itself, not a copy, not a
        serialization, not a freshly constructed instance: this method
        never instantiates anything.
        """
        raw_name = getattr(worker, "name", None)
        valid_name = isinstance(raw_name, str) and bool(raw_name)

        if not valid_name:
            recorded_name = raw_name if isinstance(raw_name, str) else repr(raw_name)
            self._ledger.append_event(
                event_type="worker_registration_rejected",
                worker_id=REGISTRY_WORKER_ID,
                payload={
                    "worker_name": recorded_name,
                    "status": "REJECTED",
                    "error_type": "INVALID_WORKER",
                },
            )
            return RegisterResult(success=False, worker_name=None, error_type="INVALID_WORKER")

        worker_name = raw_name

        if worker_name in self._workers:
            self._ledger.append_event(
                event_type="worker_registration_rejected",
                worker_id=REGISTRY_WORKER_ID,
                payload={
                    "worker_name": worker_name,
                    "status": "REJECTED",
                    "error_type": "DUPLICATE_WORKER",
                },
            )
            return RegisterResult(
                success=False, worker_name=worker_name, error_type="DUPLICATE_WORKER"
            )

        append_result = self._ledger.append_event(
            event_type="worker_registered",
            worker_id=REGISTRY_WORKER_ID,
            payload={
                "worker_name": worker_name,
                "status": "REGISTERED",
                "error_type": None,
            },
        )
        if not append_result.success:
            # No ledger event, so no in-memory registration either.
            return RegisterResult(
                success=False, worker_name=worker_name, error_type="STORAGE_FAILURE"
            )

        self._workers[worker_name] = worker
        _update_worker_projection(
            self._ledger,
            worker_name=worker_name,
            registered_at=append_result.created_at,
            last_event_id=append_result.event_id,
        )
        return RegisterResult(success=True, worker_name=worker_name)

    def get_worker(self, worker_name: str) -> Worker | None:
        """Exact, case-sensitive lookup against this process's own memory only."""
        return self._workers.get(worker_name)

    def list_workers(self) -> list[str]:
        """Every registered name, in a fixed (sorted) order -- deterministic, not insertion order."""
        return sorted(self._workers)


def _update_worker_projection(
    ledger: LedgerKernel,
    *,
    worker_name: str,
    registered_at: str,
    last_event_id: str,
) -> bool:
    """Best-effort live insert of one worker_projection row. Never raises.

    A worker name is registered exactly once, ever -- unlike
    update_intent_projection (which upserts a row that moves through
    several statuses), this only ever inserts; a failure here is not a
    problem, since rebuild_worker_projection() can always recompute the
    table from the ledger.
    """
    try:
        with ledger.connection:
            ledger.connection.execute(
                """
                INSERT OR IGNORE INTO worker_projection
                    (worker_name, registered_at, last_event_id)
                VALUES (?, ?, ?)
                """,
                (worker_name, registered_at, last_event_id),
            )
        return True
    except sqlite3.Error:
        return False


def rebuild_worker_projection(ledger: LedgerKernel) -> None:
    """Recompute worker_projection entirely from ledger_events, in one transaction.

    Same shape as leira.inbox.inbox.rebuild_intent_projection(): an
    all-or-nothing DELETE-and-replay over events in insertion order.
    Only worker_registered events ever create a row; a
    worker_registration_rejected event never does, by construction --
    a rejected registration must never appear as a registered worker.

    A worker name is immutable once registered. If the ledger somehow
    contains a second worker_registered event for the same name (e.g.
    a duplicate registration across a process restart -- see the
    module docstring), this rebuild preserves the *first* legal
    registration and ignores the later one; that later event is
    illegal history, reported by the auditor as
    DUPLICATE_WORKER_REGISTRATION, never silently applied here.
    """
    ensure_schema(ledger)
    conn = ledger.connection

    rows = conn.execute(
        "SELECT id, event_type, payload_json, created_at FROM ledger_events ORDER BY rowid"
    ).fetchall()

    seen_names: set[str] = set()

    with conn:
        conn.execute("DELETE FROM worker_projection")
        for event_id, event_type, payload_json, created_at in rows:
            if event_type != "worker_registered":
                continue
            try:
                payload = json.loads(payload_json)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            worker_name = payload.get("worker_name")
            if not isinstance(worker_name, str) or not worker_name:
                continue
            if worker_name in seen_names:
                continue

            conn.execute(
                """
                INSERT INTO worker_projection (worker_name, registered_at, last_event_id)
                VALUES (?, ?, ?)
                """,
                (worker_name, created_at, event_id),
            )
            seen_names.add(worker_name)
