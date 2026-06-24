import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.dispatcher.worker import (
    DeterministicStubWorker,
    MAX_ARTIFACT_BYTES,
    Worker,
    WorkerResult,
    WorkerRunResult,
    run_worker_once,
    validate_artifact,
    validate_context,
)
from leira.dispatcher.kernel import PayloadValidationError
from leira.dispatcher.worker import ArtifactValidationError


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


class FailingWorker:
    def wake(self, run_id: str, context: dict) -> WorkerResult:
        return WorkerResult(success=False, error_type="WORKER_FAILED", message="nope")


class BadArtifactWorker:
    def wake(self, run_id: str, context: dict) -> WorkerResult:
        return WorkerResult(success=True, artifact={"type": "text"})  # missing content


class OversizedArtifactWorker:
    def wake(self, run_id: str, context: dict) -> WorkerResult:
        return WorkerResult(
            success=True,
            artifact={"type": "text", "content": "x" * (MAX_ARTIFACT_BYTES + 1)},
        )


def test_deterministic_stub_worker_returns_stable_output():
    worker = DeterministicStubWorker()
    r1 = worker.wake("run-1", {})
    r2 = worker.wake("run-2", {"some": "context"})
    assert r1.artifact == {"type": "text", "content": "stub artifact"}
    assert r1.artifact == r2.artifact


def test_worker_result_is_typed():
    result = DeterministicStubWorker().wake("run-1", {})
    assert isinstance(result, WorkerResult)
    assert result.success
    assert isinstance(result.artifact, dict)


def test_context_rejects_non_json_safe_data():
    with pytest.raises(PayloadValidationError):
        validate_context({"score": 1.5})
    with pytest.raises(PayloadValidationError):
        validate_context({1: "bad key"})
    with pytest.raises(PayloadValidationError):
        validate_context({"obj": object()})
    with pytest.raises(PayloadValidationError):
        validate_context({"n": float("nan")})


def test_artifact_rejects_missing_type_or_content():
    with pytest.raises(ArtifactValidationError):
        validate_artifact({"content": "x"})
    with pytest.raises(ArtifactValidationError):
        validate_artifact({"type": "text"})
    with pytest.raises(ArtifactValidationError):
        validate_artifact({"type": 1, "content": "x"})
    with pytest.raises(ArtifactValidationError):
        validate_artifact(None)


def test_artifact_rejects_oversized_content():
    with pytest.raises(ArtifactValidationError):
        validate_artifact({"type": "text", "content": "x" * (MAX_ARTIFACT_BYTES + 1)})

    # Just under the limit must pass.
    validate_artifact({"type": "text", "content": "x" * 10})


def test_successful_run_records_full_lifecycle(ledger, lifecycle, run_id):
    result = run_worker_once(lifecycle, run_id, DeterministicStubWorker(), {})
    assert isinstance(result, WorkerRunResult)
    assert result.success
    assert result.current_state == "state_completed"
    assert result.artifact == {"type": "text", "content": "stub artifact"}

    rows = ledger.connection.execute(
        "SELECT event_type FROM ledger_events ORDER BY rowid"
    ).fetchall()
    event_types = [r[0] for r in rows]
    assert event_types == ["run_created", "state_running", "artifact_written", "state_completed"]

    state = lifecycle.get_run_state(run_id)
    assert state.current_state == "state_completed"


def test_worker_failure_does_not_append_state_completed(ledger, lifecycle, run_id):
    result = run_worker_once(lifecycle, run_id, FailingWorker(), {})
    assert not result.success
    assert result.error_type == "WORKER_FAILED"

    event_types = [
        r[0]
        for r in ledger.connection.execute(
            "SELECT event_type FROM ledger_events ORDER BY rowid"
        ).fetchall()
    ]
    assert "state_completed" not in event_types
    assert event_types == ["run_created", "state_running"]

    state = lifecycle.get_run_state(run_id)
    assert state.current_state == "state_running"


def test_bad_artifact_does_not_append_artifact_written_or_completed(
    ledger, lifecycle, run_id
):
    result = run_worker_once(lifecycle, run_id, BadArtifactWorker(), {})
    assert not result.success
    assert result.error_type == "INVALID_ARTIFACT"

    event_types = [
        r[0]
        for r in ledger.connection.execute(
            "SELECT event_type FROM ledger_events ORDER BY rowid"
        ).fetchall()
    ]
    assert event_types == ["run_created", "state_running"]


def test_validate_chain_still_succeeds_after_worker_run(ledger, lifecycle, run_id):
    run_worker_once(lifecycle, run_id, DeterministicStubWorker(), {})
    result = ledger.validate_chain()
    assert result.success
    assert result.events_checked == 4


def test_repeated_stub_runs_produce_identical_artifacts(ledger, lifecycle):
    run_a = lifecycle.create_run("op-1").run_id
    run_b = lifecycle.create_run("op-1").run_id

    result_a = run_worker_once(lifecycle, run_a, DeterministicStubWorker(), {})
    result_b = run_worker_once(
        lifecycle, run_b, DeterministicStubWorker(), {"different": "context"}
    )

    assert result_a.success and result_b.success
    assert result_a.artifact == result_b.artifact == {
        "type": "text",
        "content": "stub artifact",
    }
