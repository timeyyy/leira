"""Leira v0.4 shell adapter: the first external command invited in.

What this is
-------------
The smallest external adapter on top of the worker seam (worker.py): a
function that runs one external command, captures exactly what it
returned, and records that honestly through the existing run lifecycle.
Nothing here decides whether the command's output is good, nothing
retries it, and nothing routes between commands.

"The first external intelligence invited into the workshop is the
operating system. Not another mind."

Security scope (read this before trusting this for anything)
----------------------------------------------------------------
``run_command`` / ``run_shell_once`` are NOT a sandbox:
  - the command inherits the full Leira process environment (env vars,
    open file descriptors, working directory)
  - the command runs with the same filesystem permissions as the Leira
    process
  - v0.4 does not isolate secrets, filesystem access, CPU, or memory
  - commands are always passed as explicit argument lists
    (``subprocess.run(command, ...)``); the shell flag is never enabled,
    and a single shell-style string is rejected rather than executed

If you would not run a command directly in this process's shell, do not
hand it to this module.

Kernel rules
--------------
``exit_code == 0`` means the command succeeded; ``exit_code != 0`` is
simply data — it is NOT a kernel failure. A non-zero exit, like a
timeout, is still a successfully captured CommandResult, and
``run_shell_once`` still appends ``artifact_written`` and
``state_completed`` for it. The kernel only fails (and skips
``state_completed``) when the ledger append itself fails -- e.g. an
artifact that cannot be written. The kernel never interprets stdout; it
only records command, exit_code, stdout, stderr, and error_type.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from .kernel import canonicalize_payload
from .lifecycle import LifecycleKernel
from .worker import MAX_ARTIFACT_BYTES, WorkerRunResult


@dataclass(frozen=True)
class CommandResult:
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    error_type: str | None = None


def run_command(command: list[str], timeout_seconds: int = 30) -> CommandResult:
    """Run one external command and capture exactly what happened.

    command must be an explicit argument list (e.g. ["python", "--version"]),
    never a shell string. The shell flag is never enabled. Never raises
    for ordinary command failure, command-not-found, or timeout -- all
    of those are returned as a typed CommandResult.
    """
    if not isinstance(command, list) or not command or not all(
        isinstance(arg, str) for arg in command
    ):
        return CommandResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr="",
            error_type="INVALID_COMMAND",
        )

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            success=False,
            exit_code=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            error_type="TIMEOUT",
        )
    except FileNotFoundError as exc:
        return CommandResult(
            success=False,
            exit_code=127,
            stdout="",
            stderr=str(exc),
            error_type="COMMAND_NOT_FOUND",
        )
    except OSError as exc:
        return CommandResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=str(exc),
            error_type="EXECUTION_ERROR",
        )

    return CommandResult(
        success=True,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        error_type=None,
    )


def _build_command_artifact(command: list[str], result: CommandResult) -> dict:
    """Build the command_result artifact, truncating stdout/stderr deterministically.

    Repeatedly halves whichever of stdout/stderr is longer until the
    canonical JSON fits MAX_ARTIFACT_BYTES. Deterministic for identical
    inputs; no randomness, no time dependency. Marks the artifact
    ``truncated: True`` if any reduction was needed.
    """
    stdout = result.stdout
    stderr = result.stderr
    truncated = False

    while True:
        content = {
            "command": list(command),
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "error_type": result.error_type,
        }
        if truncated:
            content["truncated"] = True
        artifact = {"type": "command_result", "content": content}

        canonical = canonicalize_payload(artifact)
        if len(canonical.encode("utf-8")) <= MAX_ARTIFACT_BYTES:
            return artifact

        truncated = True
        if stdout:
            stdout = stdout[: len(stdout) // 2]
        elif stderr:
            stderr = stderr[: len(stderr) // 2]
        else:
            # Nothing left to shrink (e.g. the command list itself is
            # the oversized part). Return the best attempt; the ledger
            # layer has no size limit of its own.
            return artifact


def run_shell_once(
    lifecycle: LifecycleKernel,
    run_id: str,
    command: list[str],
    timeout_seconds: int = 30,
) -> WorkerRunResult:
    """Run one command for run_id and record the outcome through the lifecycle.

    Sequence: append state_running, execute the command, build the
    command_result artifact (enforcing MAX_ARTIFACT_BYTES), append
    artifact_written, append state_completed. A non-zero exit code or a
    timeout is recorded honestly and still completes the run -- a
    failed command is not a failed kernel. Only a failed ledger append
    skips state_completed and returns a typed failure.
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

    command_result = run_command(command, timeout_seconds=timeout_seconds)
    artifact = _build_command_artifact(command, command_result)

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
