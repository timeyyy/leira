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

v1.1 note: ``dispatch_by_name(ledger, lifecycle, registry, intent_id,
worker_name)`` resolves a name to a worker through the supplied
``leira.registry.registry.WorkerRegistry`` and delegates directly to
the unmodified ``dispatch_once`` above. The lookup is the registry's
job, not this function's: ``dispatch_by_name`` adds exactly one
``if worker is None`` check and nothing else -- no fallback, no
similar-name matching, no special path, no choice of its own.

v1.2 note: ``dispatch_and_track(ledger, lifecycle, claims, intent_id,
owner_id, worker)`` brackets the unmodified ``dispatch_once`` with the
independent ownership lock in ``leira.claims.claims.ClaimKernel`` --
claim, dispatch, release, in that fixed order. A claim failure means
``dispatch_once`` is never called. ``DispatchResult`` gained one new,
optional field, ``release_error_type`` (default ``None``, so every
existing call site and comparison is unaffected): set only when
execution finished but the post-execution release itself failed, so
the caller can see that the claim is now an orphan without this
function pretending otherwise or retrying.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.inbox.inbox import get_intent_status, update_intent_projection
from leira.workers.base import Worker, invoke_worker

if TYPE_CHECKING:
    # Type-checking only: leira.registry.registry and
    # leira.claims.claims both import leira.dispatcher.kernel, so a
    # real module-level import here would risk a circular import
    # depending on which package is imported first. Neither
    # dispatch_by_name nor dispatch_and_track ever needs the class
    # itself at runtime, only the instance the caller already built.
    from leira.claims.claims import ClaimKernel
    from leira.registry.registry import WorkerRegistry

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
    release_error_type: str | None = None


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


def dispatch_by_name(
    ledger: LedgerKernel,
    lifecycle: LifecycleKernel,
    registry: "WorkerRegistry",
    intent_id: str,
    worker_name: str,
) -> DispatchResult:
    """Resolve worker_name through registry, then delegate to dispatch_once unchanged.

    The only new behavior here is the lookup itself and the one
    ``UNKNOWN_WORKER`` failure path for a name the registry doesn't
    have. No fallback, no similar-name matching, no ranking among
    candidates, no special case: a found worker is handed straight to
    ``dispatch_once`` exactly as a caller would hand it in directly.
    """
    worker = registry.get_worker(worker_name)
    if worker is None:
        return DispatchResult(success=False, intent_id=intent_id, error_type="UNKNOWN_WORKER")
    return dispatch_once(ledger, lifecycle, intent_id, worker)


def dispatch_and_track(
    ledger: LedgerKernel,
    lifecycle: LifecycleKernel,
    claims: "ClaimKernel",
    intent_id: str,
    owner_id: str,
    worker: Worker,
) -> DispatchResult:
    """Claim, dispatch_once, release -- in that fixed order, every time.

    A claim failure means dispatch_once is never called: the claim
    store's error_type is returned as-is. dispatch_once itself is
    called completely unmodified. A release failure after execution is
    never retried and never hidden -- it is reported on
    ``release_error_type`` while the dispatch's own success/status/
    error_type are preserved exactly as dispatch_once produced them;
    the claim remains an orphan, visible via
    ``leira.claims.claims.get_claim()`` and to the auditor, until
    someone releases it explicitly.
    """
    claim_result = claims.claim_intent(intent_id, owner_id)
    if not claim_result.success:
        return DispatchResult(success=False, intent_id=intent_id, error_type=claim_result.error_type)

    result = dispatch_once(ledger, lifecycle, intent_id, worker)

    release_result = claims.release_claim(intent_id, owner_id)
    if not release_result.success:
        return DispatchResult(
            success=result.success,
            intent_id=result.intent_id,
            worker_name=result.worker_name,
            status=result.status,
            error_type=result.error_type,
            release_error_type=release_result.error_type,
        )
    return result


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
