import inspect
import sys

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.dispatcher import shell as shell_module
from leira.dispatcher.shell import (
    CommandResult,
    run_command,
    run_shell_once,
)


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
            from leira.dispatcher.lifecycle import LifecycleResult

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


def test_python_version_succeeds():
    result = run_command([sys.executable, "--version"])
    assert isinstance(result, CommandResult)
    assert result.success
    assert result.exit_code == 0
    assert result.error_type is None


def test_non_zero_exit_is_captured_and_recorded():
    result = run_command([sys.executable, "-c", "import sys; sys.exit(7)"])
    assert result.success
    assert result.exit_code == 7
    assert result.error_type is None


def test_stdout_is_captured():
    result = run_command([sys.executable, "-c", "print('hello from leira')"])
    assert result.success
    assert "hello from leira" in result.stdout


def test_stderr_is_captured():
    result = run_command(
        [sys.executable, "-c", "import sys; print('warn', file=sys.stderr)"]
    )
    assert result.success
    assert "warn" in result.stderr


def test_timeout_returns_exit_code_124_and_timeout_error():
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(2)"], timeout_seconds=1
    )
    assert not result.success
    assert result.exit_code == 124
    assert result.error_type == "TIMEOUT"


def test_oversized_output_is_handled_deterministically():
    result = run_command([sys.executable, "-c", "print('x' * 100000)"])
    assert result.success

    from leira.dispatcher.shell import _build_command_artifact
    from leira.dispatcher.worker import MAX_ARTIFACT_BYTES
    from leira.dispatcher.kernel import canonicalize_payload

    artifact_1 = _build_command_artifact(
        [sys.executable, "-c", "print('x' * 100000)"], result
    )
    artifact_2 = _build_command_artifact(
        [sys.executable, "-c", "print('x' * 100000)"], result
    )
    # Deterministic: identical input produces identical (truncated) output.
    assert artifact_1 == artifact_2
    assert artifact_1["content"]["truncated"] is True
    size = len(canonicalize_payload(artifact_1).encode("utf-8"))
    assert size <= MAX_ARTIFACT_BYTES


def test_artifact_written_is_recorded(ledger, lifecycle, run_id):
    result = run_shell_once(lifecycle, run_id, [sys.executable, "--version"])
    assert result.success
    assert "artifact_written" in _event_types(ledger)
    assert result.artifact["type"] == "command_result"
    assert result.artifact["content"]["exit_code"] == 0


def test_state_completed_recorded_after_captured_command_failure(
    ledger, lifecycle, run_id
):
    result = run_shell_once(
        lifecycle, run_id, [sys.executable, "-c", "import sys; sys.exit(7)"]
    )
    assert result.success  # a failed command is not a failed kernel
    assert result.current_state == "state_completed"
    assert _event_types(ledger) == [
        "run_created",
        "state_running",
        "artifact_written",
        "state_completed",
    ]
    assert result.artifact["content"]["exit_code"] == 7


def test_state_completed_not_recorded_if_artifact_append_fails(
    ledger, lifecycle, run_id
):
    faulty = FailingArtifactLifecycle(lifecycle)
    result = run_shell_once(faulty, run_id, [sys.executable, "--version"])
    assert not result.success
    assert result.error_type == "SIMULATED_DB_ERROR"

    event_types = _event_types(ledger)
    assert "state_completed" not in event_types
    assert event_types == ["run_created", "state_running"]


def test_validate_chain_still_succeeds_after_shell_run(ledger, lifecycle, run_id):
    run_shell_once(lifecycle, run_id, [sys.executable, "--version"])
    result = ledger.validate_chain()
    assert result.success
    assert result.events_checked == 4


def test_shell_true_is_never_used():
    source = inspect.getsource(shell_module)
    assert "shell=True" not in source


def test_command_result_is_typed():
    result = run_command([sys.executable, "--version"])
    assert isinstance(result, CommandResult)
    assert isinstance(result.success, bool)
    assert isinstance(result.exit_code, int)
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)
