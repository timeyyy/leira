"""Leira v0.2 run lifecycle: honest history, not execution.

What this is
-------------
This module records that an operation was validated, that a run was
created under that operation, and that the run moved through a small,
fixed sequence of states. It does not execute anything. It does not
decide what should happen next. It only lets the machine say, honestly,
what already happened — and refuse to record a transition that doesn't
make mechanical sense.

There is no ``runs`` table. Every lifecycle event is just another row in
the existing ``ledger_events`` table (see kernel.py). Run state is never
stored directly; it is always derived by reading the ledger and finding
the most recent event whose payload references a given run_id. Truth is
the ledger. Run state is a query.

Supported event types (and nothing else)
------------------------------------------
``operation_validated`` is an operation-level event. It is not part of a
run, and an operation may eventually have zero, one, or many runs. It is
appended once per successful envelope validation and otherwise ignored
by the run state machine below.

``run_created``, ``state_running``, ``artifact_written``, and
``state_completed`` form the run lifecycle. Allowed transitions are
represented as data (see ALLOWED_TRANSITIONS), not as branching logic
that interprets what a state "means":

    run_created       -> state_running
    state_running     -> artifact_written
    artifact_written  -> state_completed
    state_completed   -> (nothing; terminal)

Any other transition (e.g. run_created -> state_completed, or anything
out of state_completed) is mechanically rejected.

What this explicitly does NOT do
-----------------------------------
No execution. No retries. No timeouts. No cleanup. A run that ends at
state_running, or is never created at all, is not an error — the ledger
is a witness, not a supervisor. ``artifact_written`` is only an event
name; this module never reads, writes, or verifies any artifact file.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .envelope import load_and_validate
from .kernel import LedgerKernel

# The worker_id recorded against lifecycle events. There are no workers
# yet (v0.2 has none) -- this is a fixed sentinel identifying the kernel
# itself as the producer of these events.
LIFECYCLE_WORKER_ID = "kernel"

# Run-level state machine, expressed as data: current state -> allowed
# next states. The kernel enforces this mechanically; it does not
# interpret what any state means.
ALLOWED_TRANSITIONS: dict[str, list[str]] = {
    "run_created": ["state_running"],
    "state_running": ["artifact_written"],
    "artifact_written": ["state_completed"],
    "state_completed": [],
}

# Event types appendable via append_lifecycle_event. run_created is
# produced only by create_run(); operation_validated is produced only by
# validate_operation_envelope().
APPENDABLE_RUN_EVENTS = frozenset(
    {"state_running", "artifact_written", "state_completed"}
)


@dataclass(frozen=True)
class LifecycleResult:
    success: bool
    error_type: str | None = None
    message: str | None = None
    run_id: str | None = None
    current_state: str | None = None
    operation_id: str | None = None


class LifecycleKernel:
    """Run lifecycle recording on top of an existing LedgerKernel."""

    def __init__(self, ledger: LedgerKernel):
        self._ledger = ledger

    def validate_operation_envelope(self, path) -> LifecycleResult:
        """Load and structurally validate an op.yaml, then record the result.

        On success, appends an ``operation_validated`` event (operation-
        level, not part of any run). On failure, appends nothing.
        """
        contract = load_and_validate(path)
        if not contract.success:
            return LifecycleResult(
                success=False,
                error_type=contract.error_type,
                message=contract.message,
            )

        append_result = self._ledger.append_event(
            event_type="operation_validated",
            worker_id=LIFECYCLE_WORKER_ID,
            payload={"operation_id": contract.operation_id},
            operation_id=contract.operation_id,
        )
        if not append_result.success:
            return LifecycleResult(
                success=False,
                error_type=append_result.error_type,
                message=append_result.message,
                operation_id=contract.operation_id,
            )

        return LifecycleResult(
            success=True,
            operation_id=contract.operation_id,
            current_state="operation_validated",
        )

    def create_run(self, operation_id: str) -> LifecycleResult:
        """Create a new run under operation_id and append run_created.

        The new run_id is generated here and embedded in payload_json
        (along with operation_id) so that future lifecycle events and
        get_run_state() can find it.
        """
        if not isinstance(operation_id, str) or not operation_id:
            return LifecycleResult(
                success=False,
                error_type="INVALID_OPERATION_ID",
                message="operation_id must be a non-empty string",
            )

        run_id = str(uuid.uuid4())
        append_result = self._ledger.append_event(
            event_type="run_created",
            worker_id=LIFECYCLE_WORKER_ID,
            payload={"run_id": run_id, "operation_id": operation_id},
            operation_id=operation_id,
        )
        if not append_result.success:
            return LifecycleResult(
                success=False,
                error_type=append_result.error_type,
                message=append_result.message,
                operation_id=operation_id,
            )

        return LifecycleResult(
            success=True,
            run_id=run_id,
            operation_id=operation_id,
            current_state="run_created",
        )

    def append_lifecycle_event(
        self,
        run_id: str,
        event_type: str,
        extra_payload: dict | None = None,
    ) -> LifecycleResult:
        """Append the next lifecycle event for run_id, if the transition is allowed.

        Mechanical enforcement only: the current state is derived from
        the ledger, and event_type must be in ALLOWED_TRANSITIONS[current_state].
        Never interprets what a state means.

        extra_payload, if given, is merged into the event's payload
        alongside run_id (e.g. an artifact_written event carries its
        artifact this way). run_id always wins on key collision.
        """
        if event_type not in APPENDABLE_RUN_EVENTS:
            return LifecycleResult(
                success=False,
                run_id=run_id,
                error_type="UNSUPPORTED_EVENT_TYPE",
                message=f"{event_type!r} is not a valid run lifecycle event",
            )

        state_result = self.get_run_state(run_id)
        if not state_result.success:
            return state_result

        current_state = state_result.current_state
        allowed_next = ALLOWED_TRANSITIONS.get(current_state, [])
        if event_type not in allowed_next:
            return LifecycleResult(
                success=False,
                run_id=run_id,
                current_state=current_state,
                error_type="INVALID_TRANSITION",
                message=(
                    f"cannot transition run {run_id} from {current_state!r} "
                    f"to {event_type!r}"
                ),
            )

        payload = dict(extra_payload or {})
        payload["run_id"] = run_id
        append_result = self._ledger.append_event(
            event_type=event_type,
            worker_id=LIFECYCLE_WORKER_ID,
            payload=payload,
        )
        if not append_result.success:
            return LifecycleResult(
                success=False,
                run_id=run_id,
                current_state=current_state,
                error_type=append_result.error_type,
                message=append_result.message,
            )

        return LifecycleResult(
            success=True,
            run_id=run_id,
            current_state=event_type,
        )

    def get_run_state(self, run_id: str) -> LifecycleResult:
        """Derive the current state of run_id from the ledger.

        Finds the most recent ledger row whose payload_json references
        run_id and returns its event_type as current_state. State is
        never cached; this always reads the ledger.
        """
        if not isinstance(run_id, str) or not run_id:
            return LifecycleResult(
                success=False,
                error_type="INVALID_RUN_ID",
                message="run_id must be a non-empty string",
            )

        row = self._ledger.connection.execute(
            """
            SELECT event_type FROM ledger_events
            WHERE payload_json LIKE ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (f'%"run_id":"{run_id}"%',),
        ).fetchone()

        if row is None:
            return LifecycleResult(
                success=False,
                run_id=run_id,
                error_type="RUN_NOT_FOUND",
                message=f"no lifecycle events found for run_id {run_id!r}",
            )

        return LifecycleResult(success=True, run_id=run_id, current_state=row[0])
