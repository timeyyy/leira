import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel, LifecycleResult
from leira.dispatcher.worker import WorkerRunResult
from leira.workers.base import (
    EchoWorker,
    ExplodingWorker,
    FailingWorker,
    MAX_ARTIFACT_BYTES,
    Worker,
    WorkerResult,
    run_worker_once,
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


def test_echo_worker_succeeds():
    result = EchoWorker().invoke({"hello": "world"})
    assert isinstance(result, WorkerResult)
    assert result.success


def test_inputs_are_preserved(ledger, lifecycle, run_id):
    inputs = {"task": "build", "n": 3}
    result = run_worker_once(lifecycle, run_id, "echo", EchoWorker(), inputs)
    assert result.artifact["content"]["inputs"] == inputs


def test_outputs_are_preserved(ledger, lifecycle, run_id):
    inputs = {"task": "build"}
    result = run_worker_once(lifecycle, run_id, "echo", EchoWorker(), inputs)
    assert result.artifact["content"]["outputs"] == {"echo": inputs}


def test_worker_name_is_recorded(ledger, lifecycle, run_id):
    result = run_worker_once(lifecycle, run_id, "echo-1", EchoWorker(), {})
    assert result.artifact["content"]["worker_name"] == "echo-1"


def test_worker_success_recorded_separately_from_kernel_success(
    ledger, lifecycle, run_id
):
    result = run_worker_once(lifecycle, run_id, "fail", FailingWorker(), {})
    # Kernel succeeded at recording the run...
    assert result.success
    # ...even though the worker itself reported failure.
    assert result.artifact["content"]["worker_success"] is False


def test_failing_worker_is_captured_as_worker_success_false(ledger, lifecycle, run_id):
    result = run_worker_once(lifecycle, run_id, "fail", FailingWorker(), {})
    content = result.artifact["content"]
    assert content["worker_success"] is False
    assert content["error_type"] == "FAILURE"
    assert content["error_message"] == "simulated"


def test_exploding_worker_becomes_unexpected(ledger, lifecycle, run_id):
    result = run_worker_once(lifecycle, run_id, "boom", ExplodingWorker(), {})
    content = result.artifact["content"]
    assert content["worker_success"] is False
    assert content["error_type"] == "UNEXPECTED"
    assert content["error_message"] == "simulated explosion"


def test_artifact_written_is_recorded(ledger, lifecycle, run_id):
    result = run_worker_once(lifecycle, run_id, "echo", EchoWorker(), {})
    assert result.success
    assert "artifact_written" in _event_types(ledger)
    assert result.artifact["type"] == "worker_result"


def test_state_completed_recorded_after_worker_failure(ledger, lifecycle, run_id):
    result = run_worker_once(lifecycle, run_id, "fail", FailingWorker(), {})
    assert result.success
    assert result.current_state == "state_completed"
    assert _event_types(ledger) == [
        "run_created",
        "state_running",
        "artifact_written",
        "state_completed",
    ]


def test_state_completed_recorded_after_worker_exception(ledger, lifecycle, run_id):
    result = run_worker_once(lifecycle, run_id, "boom", ExplodingWorker(), {})
    assert result.success
    assert result.current_state == "state_completed"
    assert _event_types(ledger) == [
        "run_created",
        "state_running",
        "artifact_written",
        "state_completed",
    ]


def test_state_completed_not_recorded_after_artifact_append_failure(
    ledger, lifecycle, run_id
):
    faulty = FailingArtifactLifecycle(lifecycle)
    result = run_worker_once(faulty, run_id, "echo", EchoWorker(), {})
    assert not result.success
    assert result.error_type == "SIMULATED_DB_ERROR"

    event_types = _event_types(ledger)
    assert "state_completed" not in event_types
    assert event_types == ["run_created", "state_running"]


def test_validate_chain_still_succeeds(ledger, lifecycle, run_id):
    run_worker_once(lifecycle, run_id, "echo", EchoWorker(), {"a": 1})
    result = ledger.validate_chain()
    assert result.success
    assert result.events_checked == 4


def test_lifecycle_transitions_are_not_skipped(ledger, lifecycle, run_id):
    run_worker_once(lifecycle, run_id, "echo", EchoWorker(), {})
    # run_created -> state_running -> artifact_written -> state_completed,
    # every transition present, none skipped.
    assert _event_types(ledger) == [
        "run_created",
        "state_running",
        "artifact_written",
        "state_completed",
    ]


def test_run_worker_once_uses_existing_append_machinery(ledger, lifecycle, run_id):
    calls = []
    real_append = lifecycle.append_lifecycle_event

    def spy_append(run_id, event_type, extra_payload=None):
        calls.append(event_type)
        return real_append(run_id, event_type, extra_payload=extra_payload)

    lifecycle.append_lifecycle_event = spy_append
    run_worker_once(lifecycle, run_id, "echo", EchoWorker(), {})
    assert calls == ["state_running", "artifact_written", "state_completed"]


def test_oversized_artifacts_remain_valid_json(ledger, lifecycle, run_id):
    import json

    huge_inputs = {"blob": "x" * 200_000}
    result = run_worker_once(lifecycle, run_id, "echo", EchoWorker(), huge_inputs)
    assert result.success
    # Must round-trip through json without error -- valid JSON structure.
    json.loads(json.dumps(result.artifact))


def test_oversized_artifacts_truncated_deterministically(ledger, lifecycle, run_id):
    huge_inputs = {"blob": "x" * 200_000}

    result_1 = run_worker_once(lifecycle, run_id, "echo", EchoWorker(), huge_inputs)

    other_run_id = lifecycle.create_run("op-1").run_id
    result_2 = run_worker_once(
        lifecycle, other_run_id, "echo", EchoWorker(), huge_inputs
    )

    assert result_1.artifact["content"]["inputs"] == result_2.artifact["content"]["inputs"]
    assert result_1.artifact["content"]["truncated"] is True

    from leira.dispatcher.kernel import canonicalize_payload

    size = len(canonicalize_payload(result_1.artifact).encode("utf-8"))
    assert size <= MAX_ARTIFACT_BYTES


def test_truncated_artifacts_include_truncated_flag(ledger, lifecycle, run_id):
    huge_inputs = {"blob": "x" * 200_000}
    result = run_worker_once(lifecycle, run_id, "echo", EchoWorker(), huge_inputs)
    assert result.artifact["content"]["truncated"] is True

    small_result_run_id = lifecycle.create_run("op-1").run_id
    small_result = run_worker_once(
        lifecycle, small_result_run_id, "echo", EchoWorker(), {"tiny": "ok"}
    )
    assert "truncated" not in small_result.artifact["content"]


def test_worker_result_is_not_conflated_with_kernel_result(ledger, lifecycle, run_id):
    worker_result = FailingWorker().invoke({})
    assert isinstance(worker_result, WorkerResult)
    assert not isinstance(worker_result, WorkerRunResult)

    run_result = run_worker_once(lifecycle, run_id, "fail", FailingWorker(), {})
    assert isinstance(run_result, WorkerRunResult)
    assert not isinstance(run_result, WorkerResult)
    # Kernel-level success (recording worked) differs from worker-level
    # success (the work itself failed) -- both are true/false independently.
    assert run_result.success is True
    assert run_result.artifact["content"]["worker_success"] is False


def test_typed_results_preferred_over_exceptions(ledger, lifecycle, run_id):
    # ExplodingWorker raises internally, but run_worker_once never lets
    # that exception escape -- it always returns a typed result.
    result = run_worker_once(lifecycle, run_id, "boom", ExplodingWorker(), {})
    assert isinstance(result, WorkerRunResult)
    assert result.success


def test_multiple_workers_share_the_same_protocol(ledger, lifecycle):
    workers: list[Worker] = [EchoWorker(), FailingWorker(), ExplodingWorker()]
    for worker in workers:
        run_id = lifecycle.create_run("op-1").run_id
        result = run_worker_once(lifecycle, run_id, type(worker).__name__, worker, {})
        assert result.success  # kernel recording always succeeds here
        assert result.artifact["content"]["worker_name"] == type(worker).__name__
