"""Leira v0.3 worker seam: the attachment point, not the intelligence.

What this is
-------------
A "worker" in v0.3 is anything implementing the ``Worker`` protocol below:
a single synchronous method, ``wake(run_id, context) -> WorkerResult``.
This module defines that seam, a deterministic stub implementation of it
for testing, and ``run_worker_once`` — a helper that invokes a worker
exactly once and records what happened in the existing ledger via the
existing run lifecycle.

This is not orchestration and not intelligence. There is no retry logic,
no scheduler, no routing between multiple workers, no memory carried
across calls. One worker, one run, one call, recorded honestly.

Blocking call, no escape hatch
---------------------------------
``worker.wake()`` is called directly, in-process, synchronously. There
is no thread, no async event loop, no timeout, and no daemon watching
it. If a worker hangs, ``run_worker_once`` hangs, and so does the
calling process. v0.3 does not solve this — it is explicitly deferred.
Do not attach a worker here that you do not trust to return.

Context and artifact are data, not meaning
---------------------------------------------
``context`` (passed into ``wake``) and ``artifact`` (returned from it)
are validated only for *shape*: JSON-safety (no floats, no NaN/Infinity,
no non-string dict keys, no unserializable objects) and, for artifacts,
the presence of ``type``/``content`` strings and a size ceiling. Nothing
about their content is interpreted or judged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .kernel import PayloadValidationError, canonicalize_payload
from .lifecycle import LifecycleKernel

# Artifacts are metadata-sized records, not a file store. This is an
# explicit, small ceiling -- not a tuning knob.
MAX_ARTIFACT_BYTES = 64_000


class ArtifactValidationError(ValueError):
    """Raised internally when a worker's artifact fails structural validation."""


@dataclass(frozen=True)
class WorkerResult:
    success: bool
    artifact: dict | None = None
    message: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class WorkerRunResult:
    success: bool
    run_id: str | None = None
    artifact: dict | None = None
    current_state: str | None = None
    error_type: str | None = None
    message: str | None = None


class Worker(Protocol):
    def wake(self, run_id: str, context: dict) -> WorkerResult: ...


class DeterministicStubWorker:
    """A test fixture, not a mind.

    Always returns the same artifact, regardless of run_id or context.
    No randomness, no clock reads, no I/O, no external calls.
    """

    def wake(self, run_id: str, context: dict) -> WorkerResult:
        return WorkerResult(
            success=True,
            artifact={"type": "text", "content": "stub artifact"},
        )


def validate_context(context: dict) -> None:
    """Raise PayloadValidationError if context is not JSON-safe data.

    Reuses the ledger's own canonicalization rules (kernel.canonicalize_payload):
    dict only, string keys only, no floats/NaN/Infinity, no unserializable
    objects. Context is never interpreted, only checked for shape.
    """
    if not isinstance(context, dict):
        raise PayloadValidationError("context must be a dict")
    canonicalize_payload(context)


def validate_artifact(artifact: dict | None) -> str:
    """Raise ArtifactValidationError if artifact fails structural rules.

    On success, returns the canonical JSON string for the artifact (used
    both for the size check and for storage in the ledger).
    """
    if not isinstance(artifact, dict):
        raise ArtifactValidationError("artifact must be a dict")
    if not isinstance(artifact.get("type"), str):
        raise ArtifactValidationError("artifact.type must be a string")
    if not isinstance(artifact.get("content"), str):
        raise ArtifactValidationError("artifact.content must be a string")

    try:
        canonical = canonicalize_payload(artifact)
    except PayloadValidationError as exc:
        raise ArtifactValidationError(f"artifact is not JSON-safe: {exc}") from exc

    size = len(canonical.encode("utf-8"))
    if size > MAX_ARTIFACT_BYTES:
        raise ArtifactValidationError(
            f"artifact is {size} bytes, exceeds MAX_ARTIFACT_BYTES ({MAX_ARTIFACT_BYTES})"
        )

    return canonical


def run_worker_once(
    lifecycle: LifecycleKernel,
    run_id: str,
    worker: Worker,
    context: dict,
) -> WorkerRunResult:
    """Invoke worker.wake() exactly once and record the outcome honestly.

    Sequence: validate context, append state_running, call worker.wake()
    (blocking — see module docstring), validate the returned artifact,
    append artifact_written with the artifact embedded in payload_json,
    append state_completed. Returns a typed WorkerRunResult at every
    step; never raises for ordinary failures.

    If the worker fails, or the artifact is invalid, or the
    artifact_written append fails, state_completed is never appended —
    the run's last honest state is whatever was last successfully
    recorded. No retries, no cleanup.
    """
    try:
        validate_context(context)
    except PayloadValidationError as exc:
        return WorkerRunResult(
            success=False,
            run_id=run_id,
            error_type="INVALID_CONTEXT",
            message=str(exc),
        )

    running = lifecycle.append_lifecycle_event(run_id, "state_running")
    if not running.success:
        return WorkerRunResult(
            success=False,
            run_id=run_id,
            current_state=running.current_state,
            error_type=running.error_type,
            message=running.message,
        )

    # Blocking, in-process call. A hanging worker hangs here, and hangs
    # this process. v0.3 has no timeout, thread, or async to prevent that.
    worker_result = worker.wake(run_id, context)

    if not worker_result.success:
        return WorkerRunResult(
            success=False,
            run_id=run_id,
            current_state="state_running",
            error_type=worker_result.error_type or "WORKER_FAILED",
            message=worker_result.message,
        )

    try:
        validate_artifact(worker_result.artifact)
    except ArtifactValidationError as exc:
        return WorkerRunResult(
            success=False,
            run_id=run_id,
            current_state="state_running",
            error_type="INVALID_ARTIFACT",
            message=str(exc),
        )

    artifact_written = lifecycle.append_lifecycle_event(
        run_id, "artifact_written", extra_payload={"artifact": worker_result.artifact}
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
        # artifact_written really happened; do not pretend completion did.
        return WorkerRunResult(
            success=False,
            run_id=run_id,
            artifact=worker_result.artifact,
            current_state="artifact_written",
            error_type=completed.error_type,
            message=completed.message,
        )

    return WorkerRunResult(
        success=True,
        run_id=run_id,
        artifact=worker_result.artifact,
        current_state="state_completed",
    )
