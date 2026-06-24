import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel, LifecycleResult


@pytest.fixture
def ledger(tmp_path):
    db_path = tmp_path / "ledger.sqlite3"
    k = LedgerKernel(str(db_path))
    yield k
    k.close()


@pytest.fixture
def lifecycle(ledger):
    return LifecycleKernel(ledger)


def _rows(ledger):
    return ledger.connection.execute(
        "SELECT event_type, payload_json FROM ledger_events ORDER BY rowid"
    ).fetchall()


def test_create_run_creates_a_run_created_event(ledger, lifecycle):
    result = lifecycle.create_run("op-1")
    assert result.success
    assert result.run_id is not None

    rows = _rows(ledger)
    assert len(rows) == 1
    event_type, payload_json = rows[0]
    assert event_type == "run_created"
    assert result.run_id in payload_json
    assert "op-1" in payload_json


def test_valid_lifecycle_sequence_succeeds(ledger, lifecycle):
    created = lifecycle.create_run("op-1")
    run_id = created.run_id

    running = lifecycle.append_lifecycle_event(run_id, "state_running")
    assert running.success
    assert running.current_state == "state_running"

    artifact = lifecycle.append_lifecycle_event(run_id, "artifact_written")
    assert artifact.success
    assert artifact.current_state == "artifact_written"

    completed = lifecycle.append_lifecycle_event(run_id, "state_completed")
    assert completed.success
    assert completed.current_state == "state_completed"

    assert len(_rows(ledger)) == 4


def test_invalid_transitions_are_rejected(ledger, lifecycle):
    created = lifecycle.create_run("op-1")
    run_id = created.run_id

    # run_created -> state_completed (skipping state_running, artifact_written)
    result = lifecycle.append_lifecycle_event(run_id, "state_completed")
    assert not result.success
    assert result.error_type == "INVALID_TRANSITION"

    # ledger must not have grown: rejected transitions append nothing
    assert len(_rows(ledger)) == 1


def test_state_completed_cannot_transition_further(ledger, lifecycle):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")
    lifecycle.append_lifecycle_event(run_id, "artifact_written")
    completed = lifecycle.append_lifecycle_event(run_id, "state_completed")
    assert completed.success

    after = lifecycle.append_lifecycle_event(run_id, "state_running")
    assert not after.success
    assert after.error_type == "INVALID_TRANSITION"
    assert after.current_state == "state_completed"

    state = lifecycle.get_run_state(run_id)
    assert state.current_state == "state_completed"


def test_get_run_state_derives_current_state_from_ledger(ledger, lifecycle):
    run_id = lifecycle.create_run("op-1").run_id
    assert lifecycle.get_run_state(run_id).current_state == "run_created"

    lifecycle.append_lifecycle_event(run_id, "state_running")
    assert lifecycle.get_run_state(run_id).current_state == "state_running"

    lifecycle.append_lifecycle_event(run_id, "artifact_written")
    assert lifecycle.get_run_state(run_id).current_state == "artifact_written"

    lifecycle.append_lifecycle_event(run_id, "state_completed")
    assert lifecycle.get_run_state(run_id).current_state == "state_completed"

    unknown = lifecycle.get_run_state("does-not-exist")
    assert not unknown.success
    assert unknown.error_type == "RUN_NOT_FOUND"


def test_lifecycle_result_returned_instead_of_exceptions(ledger, lifecycle):
    result = lifecycle.get_run_state("nonexistent-run")
    assert isinstance(result, LifecycleResult)
    assert not result.success

    result = lifecycle.append_lifecycle_event("nonexistent-run", "state_running")
    assert isinstance(result, LifecycleResult)
    assert not result.success

    result = lifecycle.create_run("")
    assert isinstance(result, LifecycleResult)
    assert not result.success

    result = lifecycle.append_lifecycle_event("some-run", "not_a_real_event")
    assert isinstance(result, LifecycleResult)
    assert not result.success
    assert result.error_type == "UNSUPPORTED_EVENT_TYPE"


def test_lifecycle_events_are_appended_to_the_existing_ledger(ledger, lifecycle):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    rows = _rows(ledger)
    assert [r[0] for r in rows] == ["run_created", "state_running"]

    # These are genuinely rows in ledger_events, not a separate table.
    table_names = {
        row[0]
        for row in ledger.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "ledger_events" in table_names
    assert "runs" not in table_names


def test_operation_validated_remains_separate_from_run_lifecycle(
    ledger, lifecycle, tmp_path
):
    op_path = tmp_path / "op.yaml"
    op_path.write_text(
        "operation:\n"
        "  id: op-1\n"
        "  objective: x\n"
        "  success_criteria:\n"
        "    - a\n",
        encoding="utf-8",
    )

    validated = lifecycle.validate_operation_envelope(op_path)
    assert validated.success
    assert validated.operation_id == "op-1"
    assert validated.current_state == "operation_validated"

    rows = _rows(ledger)
    assert [r[0] for r in rows] == ["operation_validated"]

    # operation_validated is not a run; it must never satisfy a run_id lookup.
    state = lifecycle.get_run_state("op-1")
    assert not state.success
    assert state.error_type == "RUN_NOT_FOUND"

    # An operation may exist with zero runs. Not an error.
    run = lifecycle.create_run("op-1")
    assert run.success
    assert lifecycle.get_run_state(run.run_id).current_state == "run_created"


def test_validate_chain_still_succeeds(ledger, lifecycle, tmp_path):
    op_path = tmp_path / "op.yaml"
    op_path.write_text(
        "operation:\n"
        "  id: op-1\n"
        "  objective: x\n"
        "  success_criteria:\n"
        "    - a\n",
        encoding="utf-8",
    )

    lifecycle.validate_operation_envelope(op_path)
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")
    lifecycle.append_lifecycle_event(run_id, "artifact_written")
    lifecycle.append_lifecycle_event(run_id, "state_completed")

    result = ledger.validate_chain()
    assert result.success
    assert result.events_checked == 5
