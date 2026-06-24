"""Leira v0.6 worker protocol: the guest door, not a mind.

What this is
-------------
A "worker" here is anything implementing ``invoke(inputs: dict) ->
WorkerResult``. This module defines that protocol, three reference
workers for testing (EchoWorker, FailingWorker, ExplodingWorker), and
``run_worker_once`` — a helper that invokes a worker exactly once and
records what happened through the existing ledger, lifecycle, and
worker-run machinery (leira.dispatcher.kernel / lifecycle / worker).
It does not add a new state machine, a new table, or a new way to
write to the ledger; it reuses all of that exactly as the shell and
git adapters do.

Worker success vs. kernel success
--------------------------------------
These are deliberately two different things, and this module never
conflates them:

  - ``WorkerResult.success`` (defined here) means *the worker says the
    work succeeded*. Workers own meaning. A worker may fail, or raise,
    and that is just data about the world -- never a reason for Leira
    to lie about what happened.
  - ``WorkerRunResult`` (reused from leira.dispatcher.worker) means
    *Leira recorded the event correctly*. The kernel owns mechanics.
    Kernel failure happens only when an append to the ledger itself
    fails -- e.g. the artifact_written event could not be written.

A worker that fails, or raises an exception, still gets a fully
recorded run: state_running -> artifact_written -> state_completed.
Only a failed ledger append skips state_completed.

What this explicitly does NOT do
-----------------------------------
No prompt generation, no conversation history, no memory across calls,
no retries, no routing between workers, no scheduling, no parallel
execution, no semantic evaluation of what a worker returned. The
kernel does not understand prompts, outputs, meaning, quality,
usefulness, or correctness -- it only records worker_name, inputs,
outputs, worker_success, error_type, and error_message. A witness, not
a judge.

v1.0 note: the Worker protocol now requires a ``name`` attribute (a
stable, non-empty string). It is recorded as provenance in ledger
artifacts -- so that, six months later, the ledger can still say which
guest produced which output -- and is never used for routing or
choosing a worker; nothing in this codebase looks a worker up by name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from leira.dispatcher.kernel import PayloadValidationError, canonicalize_payload
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.dispatcher.worker import MAX_ARTIFACT_BYTES, WorkerRunResult


@dataclass(frozen=True)
class WorkerResult:
    success: bool
    outputs: dict
    error_type: str | None = None
    error_message: str = ""


class Worker(Protocol):
    name: str

    def invoke(self, inputs: dict) -> WorkerResult: ...


class EchoWorker:
    """Reference worker: succeeds, echoing inputs back as outputs."""

    name = "EchoWorker"

    def invoke(self, inputs: dict) -> WorkerResult:
        return WorkerResult(success=True, outputs={"echo": inputs})


class FailingWorker:
    """Reference worker: always reports failure, without raising."""

    name = "FailingWorker"

    def invoke(self, inputs: dict) -> WorkerResult:
        return WorkerResult(
            success=False,
            outputs={},
            error_type="FAILURE",
            error_message="simulated",
        )


class ExplodingWorker:
    """Reference worker: always raises, to exercise exception capture."""

    name = "ExplodingWorker"

    def invoke(self, inputs: dict) -> WorkerResult:
        raise RuntimeError("simulated explosion")


def invoke_worker(worker: Worker, inputs: dict) -> WorkerResult:
    """Call worker.invoke(inputs), converting any exception into a typed result.

    Normal worker failures (WorkerResult(success=False, ...)) pass
    through unchanged. An exception, or a worker returning something
    that isn't a WorkerResult, becomes error_type="UNEXPECTED" -- never
    propagated as a raised exception.
    """
    try:
        result = worker.invoke(inputs)
    except Exception as exc:
        return WorkerResult(
            success=False,
            outputs={},
            error_type="UNEXPECTED",
            error_message=str(exc),
        )

    if not isinstance(result, WorkerResult):
        return WorkerResult(
            success=False,
            outputs={},
            error_type="UNEXPECTED",
            error_message=(
                f"worker.invoke returned {type(result).__name__}, not WorkerResult"
            ),
        )

    return result


def _render(value) -> str:
    """Deterministic string form of a field, for size comparison and truncation."""
    if isinstance(value, str):
        return value
    return canonicalize_payload(value)


def _build_worker_artifact(worker_name: str, inputs: dict, result: WorkerResult) -> dict:
    """Build the worker_result artifact, truncating large fields deterministically.

    If the canonical JSON exceeds MAX_ARTIFACT_BYTES, the largest of
    inputs/outputs/error_message is replaced by a truncated string
    snapshot of its canonical form (halved repeatedly), never by
    slicing already-serialized JSON text -- the result is always valid
    JSON. Marks the artifact content ``truncated: True`` if any
    reduction was needed.
    """
    fields: dict[str, object] = {
        "inputs": inputs,
        "outputs": result.outputs,
        "error_message": result.error_message,
    }
    truncated = False

    while True:
        content = {
            "worker_name": worker_name,
            "inputs": fields["inputs"],
            "outputs": fields["outputs"],
            "worker_success": result.success,
            "error_type": result.error_type,
            "error_message": fields["error_message"],
        }
        if truncated:
            content["truncated"] = True
        artifact = {"type": "worker_result", "content": content}

        try:
            canonical = canonicalize_payload(artifact)
        except PayloadValidationError:
            # inputs/outputs are not JSON-safe data. Not this module's
            # job to police that -- the ledger's own append_event will
            # surface it as an artifact append failure when attempted.
            return artifact

        if len(canonical.encode("utf-8")) <= MAX_ARTIFACT_BYTES:
            return artifact

        truncated = True
        lengths = {name: len(_render(value)) for name, value in fields.items()}
        largest = max(lengths, key=lengths.get)
        if lengths[largest] == 0:
            # Nothing left to shrink. Return the best attempt; the
            # ledger layer has no size limit of its own.
            return artifact
        rendered = _render(fields[largest])
        fields[largest] = rendered[: len(rendered) // 2]


def run_worker_once(
    lifecycle: LifecycleKernel,
    run_id: str,
    worker_name: str,
    worker: Worker,
    inputs: dict,
) -> WorkerRunResult:
    """Invoke worker.invoke(inputs) exactly once and record the outcome honestly.

    Sequence: append state_running, invoke the worker (converting any
    exception into a typed WorkerResult), build the worker_result
    artifact (enforcing MAX_ARTIFACT_BYTES), append artifact_written,
    append state_completed. Uses the exact same append_lifecycle_event
    transition machinery as every other adapter -- no special path, no
    direct state editing. A worker failure or exception never prevents
    completion; only a failed ledger append does.
    """
    running = lifecycle.append_lifecycle_event(run_id, "state_running")
    if not running.success:
        return WorkerRunResult(
            success=False,
            run_id=run_id,
            current_state=running.current_state,
            error_type=running.error_type,
            message=running.message,
        )

    result = invoke_worker(worker, inputs)
    artifact = _build_worker_artifact(worker_name, inputs, result)

    artifact_written = lifecycle.append_lifecycle_event(
        run_id, "artifact_written", extra_payload={"artifact": artifact}
    )
    if not artifact_written.success:
        return WorkerRunResult(
            success=False,
            run_id=run_id,
            current_state="state_running",
            error_type=artifact_written.error_type,
            message=artifact_written.message,
        )

    completed = lifecycle.append_lifecycle_event(run_id, "state_completed")
    if not completed.success:
        return WorkerRunResult(
            success=False,
            run_id=run_id,
            artifact=artifact,
            current_state="artifact_written",
            error_type=completed.error_type,
            message=completed.message,
        )

    return WorkerRunResult(
        success=True,
        run_id=run_id,
        artifact=artifact,
        current_state="state_completed",
    )
