import inspect
import subprocess
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel, LifecycleResult
from leira.dispatcher import git as git_module
from leira.dispatcher import shell as shell_module
from leira.dispatcher.git import (
    GitStatusResult,
    inspect_repo,
    run_git_status_once,
)


def _run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(path: Path, branch: str = "leira-test") -> Path:
    _run(["git", "init", "-q", str(path)])
    _run(["git", "-C", str(path), "checkout", "-q", "-b", branch])
    (path / "README.txt").write_text("hello\n", encoding="utf-8")
    _run(["git", "-C", str(path), "add", "README.txt"])
    _run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Leira Test",
            "commit",
            "-q",
            "-m",
            "initial commit",
        ]
    )
    return path


@pytest.fixture
def clean_repo(tmp_path):
    return _init_repo(tmp_path / "repo")


@pytest.fixture
def ledger(tmp_path):
    k = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    yield k
    k.close()


@pytest.fixture
def lifecycle(ledger):
    return LifecycleKernel(ledger)


@pytest.fixture
def run_id(lifecycle):
    return lifecycle.create_run("op-1").run_id


def _event_types(ledger):
    return [
        r[0]
        for r in ledger.connection.execute(
            "SELECT event_type FROM ledger_events ORDER BY rowid"
        ).fetchall()
    ]


class FailingArtifactLifecycle:
    """Test double: fails only the artifact_written append, otherwise delegates."""

    def __init__(self, lifecycle: LifecycleKernel):
        self._lifecycle = lifecycle

    def append_lifecycle_event(self, run_id, event_type, extra_payload=None):
        if event_type == "artifact_written":
            return LifecycleResult(
                success=False,
                run_id=run_id,
                error_type="SIMULATED_DB_ERROR",
                message="simulated artifact append failure",
            )
        return self._lifecycle.append_lifecycle_event(
            run_id, event_type, extra_payload=extra_payload
        )

    def get_run_state(self, run_id):
        return self._lifecycle.get_run_state(run_id)


def test_clean_repository_records_is_dirty_false(clean_repo):
    result = inspect_repo(str(clean_repo))
    assert result.success
    assert result.is_dirty is False
    assert result.status_porcelain == ""


def test_dirty_repository_records_is_dirty_true(clean_repo):
    (clean_repo / "untracked.txt").write_text("oops\n", encoding="utf-8")
    result = inspect_repo(str(clean_repo))
    assert result.success
    assert result.is_dirty is True
    assert "untracked.txt" in result.status_porcelain


def test_head_sha_captured(clean_repo):
    expected = _run(["git", "-C", str(clean_repo), "rev-parse", "HEAD"]).stdout.strip()
    result = inspect_repo(str(clean_repo))
    assert result.head_sha == expected
    assert len(result.head_sha) == 40


def test_branch_captured(clean_repo):
    result = inspect_repo(str(clean_repo))
    assert result.branch == "leira-test"


def test_status_porcelain_captured_exactly(clean_repo):
    (clean_repo / "exact.txt").write_text("x\n", encoding="utf-8")
    expected = _run(
        ["git", "-C", str(clean_repo), "status", "--porcelain"]
    ).stdout
    result = inspect_repo(str(clean_repo))
    assert result.status_porcelain == expected


def test_detached_head_handled(clean_repo):
    sha = _run(["git", "-C", str(clean_repo), "rev-parse", "HEAD"]).stdout.strip()
    _run(["git", "-C", str(clean_repo), "checkout", "-q", sha])

    result = inspect_repo(str(clean_repo))
    assert result.success
    assert result.branch is None
    assert result.head_sha == sha
    assert result.error_type is None


def test_non_repository_returns_not_repository(tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    result = inspect_repo(str(not_a_repo))
    assert not result.success
    assert result.error_type == "NOT_REPOSITORY"


def test_missing_git_executable_returns_git_not_found(tmp_path, monkeypatch):
    empty_dir = tmp_path / "no_git_here"
    empty_dir.mkdir()
    monkeypatch.setenv("PATH", str(empty_dir))

    result = inspect_repo(str(tmp_path))
    assert not result.success
    assert result.error_type == "GIT_NOT_FOUND"


def test_timeout_returns_timeout(monkeypatch, clean_repo):
    from leira.dispatcher.shell import CommandResult

    def fake_run_command(command, timeout_seconds=30):
        return CommandResult(
            success=False, exit_code=124, stdout="", stderr="", error_type="TIMEOUT"
        )

    monkeypatch.setattr(git_module, "run_command", fake_run_command)
    result = inspect_repo(str(clean_repo), timeout_seconds=1)
    assert not result.success
    assert result.error_type == "TIMEOUT"


def test_artifact_written_is_recorded(ledger, lifecycle, run_id, clean_repo):
    result = run_git_status_once(lifecycle, run_id, str(clean_repo))
    assert result.success
    assert "artifact_written" in _event_types(ledger)
    assert result.artifact["type"] == "git_status"
    assert result.artifact["content"]["is_dirty"] is False


def test_state_completed_recorded_after_inspection_failure(
    ledger, lifecycle, run_id, tmp_path
):
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()

    result = run_git_status_once(lifecycle, run_id, str(not_a_repo))
    assert result.success  # a failed inspection is not a failed kernel
    assert result.current_state == "state_completed"
    assert _event_types(ledger) == [
        "run_created",
        "state_running",
        "artifact_written",
        "state_completed",
    ]
    assert result.artifact["content"]["error_type"] == "NOT_REPOSITORY"


def test_state_completed_not_recorded_when_artifact_append_fails(
    ledger, lifecycle, run_id, clean_repo
):
    faulty = FailingArtifactLifecycle(lifecycle)
    result = run_git_status_once(faulty, run_id, str(clean_repo))
    assert not result.success
    assert result.error_type == "SIMULATED_DB_ERROR"

    event_types = _event_types(ledger)
    assert "state_completed" not in event_types
    assert event_types == ["run_created", "state_running"]


def test_validate_chain_still_succeeds(ledger, lifecycle, run_id, clean_repo):
    run_git_status_once(lifecycle, run_id, str(clean_repo))
    result = ledger.validate_chain()
    assert result.success
    assert result.events_checked == 4


def test_shell_adapter_reused(monkeypatch, clean_repo):
    calls = []
    real_run_command = shell_module.run_command

    def counting_run_command(command, timeout_seconds=30):
        calls.append(command)
        return real_run_command(command, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(git_module, "run_command", counting_run_command)
    inspect_repo(str(clean_repo))

    assert len(calls) == 4
    assert all(c[0] == "git" for c in calls)
    assert "subprocess" not in inspect.getsource(git_module)


def test_shell_true_is_never_used():
    source = inspect.getsource(git_module)
    assert "shell=True" not in source


def test_typed_failures_used_instead_of_exceptions(tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    result = inspect_repo(str(not_a_repo))
    assert isinstance(result, GitStatusResult)
    assert not result.success
    assert result.error_type == "NOT_REPOSITORY"


def test_oversized_artifacts_truncated_deterministically(clean_repo):
    huge_name_dir = clean_repo
    # Create enough untracked files with long names to push
    # status --porcelain output past MAX_ARTIFACT_BYTES.
    for i in range(2000):
        (huge_name_dir / f"untracked_file_number_{i:06d}_padding.txt").touch()

    from leira.dispatcher.git import _build_git_artifact
    from leira.dispatcher.worker import MAX_ARTIFACT_BYTES
    from leira.dispatcher.kernel import canonicalize_payload

    result = inspect_repo(str(huge_name_dir))
    assert result.is_dirty

    artifact_1 = _build_git_artifact(result)
    artifact_2 = _build_git_artifact(result)
    assert artifact_1 == artifact_2
    assert artifact_1["content"]["truncated"] is True
    size = len(canonicalize_payload(artifact_1).encode("utf-8"))
    assert size <= MAX_ARTIFACT_BYTES
