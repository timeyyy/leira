"""Leira v1.0 single dispatcher: one intent, one worker, one honest record.

What this is
-------------
``dispatch_once(ledger, lifecycle, intent_id, worker)`` is a mechanical
actuator, not a broker. The caller supplies both the intent to claim
and the worker to run it with, explicitly, every time. There is no
lookup, no choice, no routing: if you want to dispatch one hundred
intents, you call ``dispatch_once`` one hundred times. This file
contains no loop over intents and never will.

Architecture: Inbox -> Dispatcher -> Worker -> Ledger -> Projection.
The dispatcher reads an intent's current status straight from the
ledger (``leira.inbox.inbox.get_intent_status`` -- the same
"authoritative answer always comes from history" pattern as
``LifecycleKernel.get_run_state()``), invokes the supplied worker
exactly once, and records what happened. Execution itself is wrapped
in a real run, created and advanced through the *unmodified*
``LifecycleKernel`` (``create_run`` / ``append_lifecycle_event`` for
``state_running``/``artifact_written``) -- no new state machine, no
duplicated transition logic. Only the intent's own terminal outcome
(``intent_claimed`` -> ``intent_completed``/``intent_failed``) is new,
appended directly through the unmodified ``LedgerKernel.append_event``.

Claim rules
-------------
Only a ``PENDING`` intent may be claimed. ``RUNNING``, ``COMPLETED``,
``FAILED`` (and ``REJECTED``) are all refused with
``error_type="INVALID_STATUS"`` -- including a second attempt to
dispatch an already-terminal intent: there is no double execution, no
duplicate claim, no terminal mutation. An intent_id that was never
submitted at all returns ``error_type="UNKNOWN_INTENT"``. A worker
with no usable ``name`` returns ``error_type="INVALID_WORKER"`` before
anything is claimed or appended.

Worker failure vs. dispatcher failure
------------------------------------------
Exactly the same split as every other adapter in this codebase: a
worker that returns failure, or raises (caught and converted to
``error_type="UNEXPECTED"`` via ``leira.workers.base.invoke_worker`` --
reused, not reimplemented), still gets a fully recorded dispatch
ending in ``intent_failed``/``FAILED``. ``dispatch_once`` reports
``success=True`` for that outcome: the *dispatcher* did its job
correctly. Only a failed ledger append along the way is a dispatcher
failure (``success=False``).

What this explicitly does NOT do
-----------------------------------
No routing, no table of known workers, no worker lookup, no
scheduling, no repeated attempts on failure, no priorities, no queues,
no load balancing, no memory across calls, no parallel execution, no
loop inside ``dispatch_once``. ``worker.name`` is recorded purely as
provenance in the artifact and in
``intent_claimed``/``intent_completed``/``intent_failed`` -- nothing in
this module ever reads it back to decide what to do.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.inbox.inbox import get_intent_status, update_intent_projection
from leira.workers.base import Worker, invoke_worker

# Worker id recorded against dispatcher-produced ledger events that are
# not themselves a worker's own artifact (intent_claimed/completed/failed).
DISPATCHER_WORKER_ID = "kernel"


@dataclass(frozen=True)
class DispatchResult:
    success: bool
    intent_id: str
    worker_name: str | None = None
    status: str | None = None
    error_type: str | None = None


def dispatch_once(
    ledger: LedgerKernel,
    lifecycle: LifecycleKernel,
    intent_id: str,
    worker: Worker,
) -> DispatchResult:
    """Claim exactly one PENDING intent and run it with exactly one worker.

    No internal loop, no repeated attempts, no lookup. Every ledger append below
    uses existing, unmodified machinery (LedgerKernel.append_event,
    LifecycleKernel.create_run/append_lifecycle_event,
    leira.workers.base.invoke_worker) -- this function only sequences
    those calls and decides, mechanically, whether to keep going.
    """
    # 1 & 2: read status, verify it is claimable.
    status = get_intent_status(ledger, intent_id)
    if status is None:
        return DispatchResult(success=False, intent_id=intent_id, error_type="UNKNOWN_INTENT")
    if status != "PENDING":
        return DispatchResult(
            success=False, intent_id=intent_id, status=status, error_type="INVALID_STATUS"
        )

    # 3: read intent payload, straight from its intent_submitted event.
    payload = _read_intent_payload(ledger, intent_id)
    if payload is None:
        return DispatchResult(success=False, intent_id=intent_id, error_type="UNKNOWN_INTENT")

    # 4: validate worker.name. Provenance metadata only -- never used
    # to look up or choose a worker.
    worker_name = getattr(worker, "name", None)
    if not isinstance(worker_name, str) or not worker_name:
        return DispatchResult(success=False, intent_id=intent_id, error_type="INVALID_WORKER")

    # 5: append intent_claimed (status=RUNNING, worker_name).
    claimed = ledger.append_event(
        event_type="intent_claimed",
        worker_id=DISPATCHER_WORKER_ID,
        payload={"intent_id": intent_id, "status": "RUNNING", "worker_name": worker_name},
    )
    if not claimed.success:
        return DispatchResult(
            success=False,
            intent_id=intent_id,
            worker_name=worker_name,
            error_type=claimed.error_type or "STORAGE_FAILURE",
        )
    update_intent_projection(
        ledger,
        intent_id=intent_id,
        status="RUNNING",
        worker_name=worker_name,
        last_event_id=claimed.event_id,
        updated_at=claimed.created_at,
    )

    # 6: a real run, created and advanced through the unmodified
    # LifecycleKernel -- state_running is the existing run-lifecycle
    # event, reused exactly as every other adapter reuses it.
    run = lifecycle.create_run(operation_id=intent_id)
    if not run.success:
        return DispatchResult(
            success=False,
            intent_id=intent_id,
            worker_name=worker_name,
            status="RUNNING",
            error_type=run.error_type or "STORAGE_FAILURE",
        )
    run_id = run.run_id

    running = lifecycle.append_lifecycle_event(run_id, "state_running")
    if not running.success:
        return DispatchResult(
            success=False,
            intent_id=intent_id,
            worker_name=worker_name,
            status="RUNNING",
            error_type=running.error_type or "STORAGE_FAILURE",
        )

    # 7 & 8: invoke the supplied worker exactly once; any exception is
    # converted to a typed WorkerResult, never raised.
    worker_result = invoke_worker(worker, payload)

    # 9: append artifact_written, carrying intent provenance.
    artifact = {
        "type": "worker_result",
        "content": {
            "intent_id": intent_id,
            "worker_name": worker_name,
            "inputs": payload,
            "outputs": worker_result.outputs,
            "worker_success": worker_result.success,
            "error_type": worker_result.error_type,
            "error_message": worker_result.error_message,
        },
    }
    artifact_written = lifecycle.append_lifecycle_event(
        run_id, "artifact_written", extra_payload={"artifact": artifact}
    )
    if not artifact_written.success:
        return DispatchResult(
            success=False,
            intent_id=intent_id,
            worker_name=worker_name,
            status="RUNNING",
            error_type=artifact_written.error_type or "STORAGE_FAILURE",
        )

    # 10: terminal intent event. Worker failure is not dispatcher
    # failure -- both branches are a *successful* dispatch.
    final_status = "COMPLETED" if worker_result.success else "FAILED"
    final_event_type = "intent_completed" if worker_result.success else "intent_failed"
    final_append = ledger.append_event(
        event_type=final_event_type,
        worker_id=DISPATCHER_WORKER_ID,
        payload={
            "intent_id": intent_id,
            "status": final_status,
            "worker_name": worker_name,
            "error_type": worker_result.error_type,
            "error_message": worker_result.error_message,
        },
    )
    if not final_append.success:
        return DispatchResult(
            success=False,
            intent_id=intent_id,
            worker_name=worker_name,
            status="RUNNING",
            error_type=final_append.error_type or "STORAGE_FAILURE",
        )
    update_intent_projection(
        ledger,
        intent_id=intent_id,
        status=final_status,
        worker_name=worker_name,
        last_event_id=final_append.event_id,
        updated_at=final_append.created_at,
    )

    return DispatchResult(
        success=True,
        intent_id=intent_id,
        worker_name=worker_name,
        status=final_status,
    )


def _read_intent_payload(ledger: LedgerKernel, intent_id: str) -> dict | None:
    """Read intent_id's original payload straight from its intent_submitted event.

    Read-only; never consults inbox_entries (an ingress convenience
    table) so that the only source of truth for what was actually
    requested is, as everywhere else in this system, the ledger.
    """
    row = ledger.connection.execute(
        """
        SELECT payload_json FROM ledger_events
        WHERE event_type = 'intent_submitted'
        AND payload_json LIKE ?
        ORDER BY rowid ASC
        LIMIT 1
        """,
        (f'%"intent_id":"{intent_id}"%',),
    ).fetchone()
    if row is None:
        return None
    try:
        event_payload = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    payload = event_payload.get("payload") if isinstance(event_payload, dict) else None
    return payload if isinstance(payload, dict) else None
