import json

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.projection import rebuild as rebuild_module
from leira.projection.rebuild import rebuild_projection
from leira.projection.state import ProjectionEngine


@pytest.fixture
def ledger(tmp_path):
    k = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    yield k
    k.close()


@pytest.fixture
def projection(ledger):
    return ProjectionEngine(ledger)


@pytest.fixture
def lifecycle(ledger, projection):
    return LifecycleKernel(ledger, projection=projection)


def _projection_rows(ledger):
    return ledger.connection.execute(
        "SELECT run_id, current_state, last_event_id, updated_at "
        "FROM operation_state_projection ORDER BY run_id"
    ).fetchall()


def test_projection_reflects_run_created(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    assert projection.get_current_state(run_id) == "run_created"


def test_projection_reflects_state_running(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")
    assert projection.get_current_state(run_id) == "state_running"


def test_projection_reflects_artifact_written(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")
    lifecycle.append_lifecycle_event(run_id, "artifact_written")
    assert projection.get_current_state(run_id) == "artifact_written"


def test_projection_reflects_state_completed(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")
    lifecycle.append_lifecycle_event(run_id, "artifact_written")
    lifecycle.append_lifecycle_event(run_id, "state_completed")
    assert projection.get_current_state(run_id) == "state_completed"


def test_multiple_operations_coexist(ledger, lifecycle, projection):
    run_a = lifecycle.create_run("op-a").run_id
    run_b = lifecycle.create_run("op-b").run_id
    lifecycle.append_lifecycle_event(run_a, "state_running")

    assert projection.get_current_state(run_a) == "state_running"
    assert projection.get_current_state(run_b) == "run_created"


def test_current_state_equals_most_recent_event(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")
    lifecycle.append_lifecycle_event(run_id, "artifact_written")

    expected = lifecycle.get_run_state(run_id).current_state
    assert projection.get_current_state(run_id) == expected


def test_last_event_id_derived_from_ledger(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    result = lifecycle.append_lifecycle_event(run_id, "state_running")

    row = ledger.connection.execute(
        "SELECT last_event_id FROM operation_state_projection WHERE run_id = ?",
        (run_id,),
    ).fetchone()

    ledger_row = ledger.connection.execute(
        "SELECT id FROM ledger_events WHERE event_type = 'state_running' "
        "AND payload_json LIKE ?",
        (f'%"run_id":"{run_id}"%',),
    ).fetchone()
    assert row[0] == ledger_row[0]


def test_updated_at_comes_from_ledger_timestamp(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    proj_row = ledger.connection.execute(
        "SELECT updated_at FROM operation_state_projection WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    ledger_row = ledger.connection.execute(
        "SELECT created_at FROM ledger_events WHERE event_type = 'state_running' "
        "AND payload_json LIKE ?",
        (f'%"run_id":"{run_id}"%',),
    ).fetchone()
    assert proj_row[0] == ledger_row[0]


def test_projection_contains_no_hidden_state(ledger, projection):
    columns = [
        row[1]
        for row in ledger.connection.execute(
            "PRAGMA table_info(operation_state_projection)"
        ).fetchall()
    ]
    assert set(columns) == {"run_id", "current_state", "last_event_id", "updated_at"}


def test_deleting_projection_loses_no_truth(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    ledger.connection.execute("DELETE FROM operation_state_projection")
    ledger.connection.commit()

    assert projection.get_current_state(run_id) is None
    # The ledger itself is untouched -- truth survives projection loss.
    assert lifecycle.get_run_state(run_id).current_state == "state_running"
    result = ledger.validate_chain()
    assert result.success


def test_rebuild_restores_deleted_projections(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    ledger.connection.execute("DELETE FROM operation_state_projection")
    ledger.connection.commit()
    assert projection.get_current_state(run_id) is None

    rebuild_projection(ledger)
    assert projection.get_current_state(run_id) == "state_running"


def test_rebuild_reproduces_previous_state_exactly(ledger, lifecycle, projection):
    run_a = lifecycle.create_run("op-a").run_id
    lifecycle.append_lifecycle_event(run_a, "state_running")
    run_b = lifecycle.create_run("op-b").run_id
    lifecycle.append_lifecycle_event(run_b, "state_running")
    lifecycle.append_lifecycle_event(run_b, "artifact_written")

    before = _projection_rows(ledger)

    ledger.connection.execute("DELETE FROM operation_state_projection")
    ledger.connection.commit()

    rebuild_projection(ledger)
    after = _projection_rows(ledger)

    assert before == after


def test_rebuild_is_idempotent(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    rebuild_projection(ledger)
    first = _projection_rows(ledger)
    rebuild_projection(ledger)
    second = _projection_rows(ledger)

    assert first == second
    assert len(first) == 1


def test_projection_corruption_is_recoverable(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    # Corrupt the projection directly (never via the kernel).
    ledger.connection.execute(
        "UPDATE operation_state_projection SET current_state = 'bogus', "
        "last_event_id = 'bogus', updated_at = 'bogus' WHERE run_id = ?",
        (run_id,),
    )
    ledger.connection.commit()
    assert projection.get_current_state(run_id) == "bogus"

    rebuild_projection(ledger)
    assert projection.get_current_state(run_id) == "state_running"


def test_projection_disagreement_favors_ledger(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    ledger.connection.execute(
        "UPDATE operation_state_projection SET current_state = 'state_completed' "
        "WHERE run_id = ?",
        (run_id,),
    )
    ledger.connection.commit()

    # The projection now lies. The ledger-derived state never does --
    # get_run_state always recomputes from ledger_events directly.
    assert projection.get_current_state(run_id) == "state_completed"
    assert lifecycle.get_run_state(run_id).current_state == "state_running"


def test_validate_chain_still_succeeds(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")
    lifecycle.append_lifecycle_event(run_id, "artifact_written")
    lifecycle.append_lifecycle_event(run_id, "state_completed")

    result = ledger.validate_chain()
    assert result.success
    assert result.events_checked == 4


def test_unknown_run_returns_none(projection):
    assert projection.get_current_state("no-such-run") is None


def test_first_event_creates_projection(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    assert projection.get_current_state(run_id) is not None
    assert projection.get_current_state(run_id) == "run_created"


def test_rebuild_uses_a_transaction(ledger, lifecycle, projection, monkeypatch):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    real_loads = json.loads
    calls = {"n": 0}

    class _FailAfter:
        def loads(self, s):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated rebuild failure")
            return real_loads(s)

    before = _projection_rows(ledger)
    monkeypatch.setattr(rebuild_module, "json", _FailAfter())

    with pytest.raises(RuntimeError):
        rebuild_projection(ledger)

    monkeypatch.undo()
    after = _projection_rows(ledger)
    # The transaction rolled back: the table is exactly as it was
    # before the failed rebuild attempt, never partially rebuilt.
    assert before == after


def test_partial_rebuilds_are_impossible(ledger, lifecycle, projection, monkeypatch):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")
    other_run_id = lifecycle.create_run("op-2").run_id

    real_loads = json.loads
    calls = {"n": 0}

    class _FailAfter:
        def loads(self, s):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("simulated rebuild failure")
            return real_loads(s)

    before = _projection_rows(ledger)
    monkeypatch.setattr(rebuild_module, "json", _FailAfter())

    with pytest.raises(RuntimeError):
        rebuild_projection(ledger)
    monkeypatch.undo()

    after = _projection_rows(ledger)
    assert after == before
    # Both runs are still individually queryable and unaffected.
    assert projection.get_current_state(run_id) == "state_running"
    assert projection.get_current_state(other_run_id) == "run_created"


def test_event_replay(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")
    lifecycle.append_lifecycle_event(run_id, "artifact_written")
    lifecycle.append_lifecycle_event(run_id, "state_completed")

    before = projection.get_current_state(run_id)
    assert before == "state_completed"

    ledger.connection.execute("DELETE FROM operation_state_projection")
    ledger.connection.commit()
    assert projection.get_current_state(run_id) is None

    rebuild_projection(ledger)
    after = projection.get_current_state(run_id)
    assert after == before
