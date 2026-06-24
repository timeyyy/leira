"""Leira v0.5 git adapter: repository witness, not project manager.

What this is
-------------
The smallest read-only inspector of a Git repository's current state:
HEAD commit, current branch (if any), and whether the working tree is
clean or dirty, exactly as Git reports them. Built entirely on top of
the existing shell adapter (``shell.run_command``) — this module never
spawns a process of its own and does not duplicate that process
handling.

"The shell introduced the operating system. Git introduces provenance.
Before inviting another mind, the workshop should learn to remember
itself."

What this explicitly does NOT do
-----------------------------------
This module never mutates a repository: no ``git add``, ``commit``,
``push``, ``pull``, ``fetch``, ``merge``, or ``rebase``. It does not
parse diffs, does not interpret ``.gitignore``, does not filter files,
and does not cache or schedule anything. A dirty working tree, a
detached HEAD, or a path that isn't a repository at all are not kernel
failures — they are exactly what ``inspect_repo`` is here to record.
Large repositories may make ``git status --porcelain`` slow; that is
inherited honestly, not optimized away. Truth outranks responsiveness.
"""

from __future__ import annotations

from dataclasses import dataclass

from .kernel import canonicalize_payload
from .lifecycle import LifecycleKernel
from .shell import run_command
from .worker import MAX_ARTIFACT_BYTES, WorkerRunResult


@dataclass(frozen=True)
class GitStatusResult:
    success: bool
    repo_path: str
    head_sha: str | None
    branch: str | None
    is_dirty: bool | None
    status_porcelain: str
    stderr: str
    error_type: str | None = None


def _map_command_error(error_type: str | None) -> str | None:
    """Translate a shell-adapter CommandResult.error_type into git-adapter terms."""
    if error_type == "COMMAND_NOT_FOUND":
        return "GIT_NOT_FOUND"
    if error_type == "TIMEOUT":
        return "TIMEOUT"
    if error_type in ("EXECUTION_ERROR", "INVALID_COMMAND"):
        return "UNEXPECTED"
    return None


def inspect_repo(repo_path: str, timeout_seconds: int = 30) -> GitStatusResult:
    """Read-only inspection of repo_path's current Git state.

    Runs four explicit, read-only git commands via the shell adapter's
    run_command -- never a process spawned directly here, and the shell
    flag is never enabled. Never raises for ordinary git failures; a
    non-repository path, a missing git executable, or a timeout all
    become typed GitStatusResult failures instead.
    """
    toplevel = run_command(
        ["git", "-C", repo_path, "rev-parse", "--show-toplevel"],
        timeout_seconds=timeout_seconds,
    )
    mapped = _map_command_error(toplevel.error_type)
    if mapped is not None:
        return GitStatusResult(
            success=False,
            repo_path=repo_path,
            head_sha=None,
            branch=None,
            is_dirty=None,
            status_porcelain="",
            stderr=toplevel.stderr,
            error_type=mapped,
        )
    if toplevel.exit_code != 0:
        return GitStatusResult(
            success=False,
            repo_path=repo_path,
            head_sha=None,
            branch=None,
            is_dirty=None,
            status_porcelain="",
            stderr=toplevel.stderr,
            error_type="NOT_REPOSITORY",
        )

    head = run_command(
        ["git", "-C", repo_path, "rev-parse", "HEAD"], timeout_seconds=timeout_seconds
    )
    mapped = _map_command_error(head.error_type)
    if mapped is not None:
        return GitStatusResult(
            success=False,
            repo_path=repo_path,
            head_sha=None,
            branch=None,
            is_dirty=None,
            status_porcelain="",
            stderr=head.stderr,
            error_type=mapped,
        )

    branch = run_command(
        ["git", "-C", repo_path, "branch", "--show-current"],
        timeout_seconds=timeout_seconds,
    )
    mapped = _map_command_error(branch.error_type)
    if mapped is not None:
        return GitStatusResult(
            success=False,
            repo_path=repo_path,
            head_sha=None,
            branch=None,
            is_dirty=None,
            status_porcelain="",
            stderr=branch.stderr,
            error_type=mapped,
        )

    status = run_command(
        ["git", "-C", repo_path, "status", "--porcelain"],
        timeout_seconds=timeout_seconds,
    )
    mapped = _map_command_error(status.error_type)
    if mapped is not None:
        return GitStatusResult(
            success=False,
            repo_path=repo_path,
            head_sha=None,
            branch=None,
            is_dirty=None,
            status_porcelain="",
            stderr=status.stderr,
            error_type=mapped,
        )

    # Git decides what exists; Leira preserves what Git said. A failed
    # `rev-parse HEAD` (e.g. no commits yet) just means head_sha is
    # unknown, not that inspection failed. An empty --show-current is a
    # detached HEAD, not a missing branch -- recorded as None, not an error.
    head_sha = head.stdout.strip() if head.exit_code == 0 and head.stdout.strip() else None
    branch_name = branch.stdout.strip() or None
    status_porcelain = status.stdout
    is_dirty = len(status_porcelain) > 0
    stderr = "\n".join(filter(None, (head.stderr, branch.stderr, status.stderr)))

    return GitStatusResult(
        success=True,
        repo_path=repo_path,
        head_sha=head_sha,
        branch=branch_name,
        is_dirty=is_dirty,
        status_porcelain=status_porcelain,
        stderr=stderr,
        error_type=None,
    )


def _build_git_artifact(result: GitStatusResult) -> dict:
    """Build the git_status artifact, truncating long fields deterministically.

    Repeatedly halves whichever of status_porcelain/stderr is longer
    until the canonical JSON fits MAX_ARTIFACT_BYTES. Deterministic for
    identical inputs. Marks the artifact ``truncated: True`` if any
    reduction was needed.
    """
    status_porcelain = result.status_porcelain
    stderr = result.stderr
    truncated = False

    while True:
        content = {
            "repo_path": result.repo_path,
            "head_sha": result.head_sha,
            "branch": result.branch,
            "is_dirty": result.is_dirty,
            "status_porcelain": status_porcelain,
            "stderr": stderr,
            "error_type": result.error_type,
        }
        if truncated:
            content["truncated"] = True
        artifact = {"type": "git_status", "content": content}

        canonical = canonicalize_payload(artifact)
        if len(canonical.encode("utf-8")) <= MAX_ARTIFACT_BYTES:
            return artifact

        truncated = True
        if len(status_porcelain) >= len(stderr) and status_porcelain:
            status_porcelain = status_porcelain[: len(status_porcelain) // 2]
        elif stderr:
            stderr = stderr[: len(stderr) // 2]
        else:
            # Nothing left to shrink. Return the best attempt; the
            # ledger layer has no size limit of its own.
            return artifact


def run_git_status_once(
    lifecycle: LifecycleKernel,
    run_id: str,
    repo_path: str,
    timeout_seconds: int = 30,
) -> WorkerRunResult:
    """Inspect repo_path for run_id and record the outcome through the lifecycle.

    Sequence: append state_running, inspect the repository, build the
    git_status artifact (enforcing MAX_ARTIFACT_BYTES), append
    artifact_written, append state_completed. A non-repository, a
    missing git executable, or a timeout is recorded honestly and still
    completes the run. Only a failed ledger append skips
    state_completed and returns a typed failure.
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

    git_result = inspect_repo(repo_path, timeout_seconds=timeout_seconds)
    artifact = _build_git_artifact(git_result)

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
