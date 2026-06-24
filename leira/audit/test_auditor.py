import json
import uuid

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.projection.state import ProjectionEngine
from leira.audit import auditor as auditor_module
from leira.audit.auditor import AuditResult, audit


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


def _snapshot(ledger):
    events = ledger.connection.execute(
        "SELECT * FROM ledger_events ORDER BY rowid"
    ).fetchall()
    try:
        proj = ledger.connection.execute(
            "SELECT * FROM operation_state_projection ORDER BY run_id"
        ).fetchall()
    except Exception:
        proj = []
    return events, proj


def _full_run(lifecycle, operation_id="op-1"):
    run_id = lifecycle.create_run(operation_id).run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")
    lifecycle.append_lifecycle_event(run_id, "artifact_written", extra_payload={"artifact": {"type": "text", "content": "hi"}})
    lifecycle.append_lifecycle_event(run_id, "state_completed")
    return run_id


# 1. valid chain passes
def test_valid_chain_passes(ledger, lifecycle):
    _full_run(lifecycle)
    result = audit(ledger)
    assert isinstance(result, AuditResult)
    assert result.success
    assert result.chain_valid
    assert result.projections_valid
    assert result.errors == []


# 2. broken hash detected
def test_broken_hash_detected(ledger, lifecycle):
    run_id = _full_run(lifecycle)
    row = ledger.connection.execute(
        "SELECT id FROM ledger_events ORDER BY rowid LIMIT 1"
    ).fetchone()
    event_id = row[0]

    ledger.connection.execute("DROP TRIGGER trg_ledger_events_no_update")
    ledger.connection.execute(
        "UPDATE ledger_events SET payload_json = '{\"tampered\":true}' WHERE id = ?",
        (event_id,),
    )
    ledger.connection.commit()

    result = audit(ledger)
    assert not result.success
    assert not result.chain_valid
    assert any(e.startswith("BROKEN_HASH_CHAIN:") for e in result.errors)


# 3. missing previous_hash detected
def test_missing_previous_hash_detected(ledger, lifecycle):
    run_id = _full_run(lifecycle)
    rows = ledger.connection.execute(
        "SELECT id FROM ledger_events ORDER BY rowid"
    ).fetchall()
    second_event_id = rows[1][0]

    ledger.connection.execute("DROP TRIGGER trg_ledger_events_no_update")
    ledger.connection.execute(
        "UPDATE ledger_events SET parent_event_hash = 'bogus-hash' WHERE id = ?",
        (second_event_id,),
    )
    ledger.connection.commit()

    result = audit(ledger)
    assert not result.success
    assert not result.chain_valid
    assert any(e.startswith("MISSING_PREVIOUS_HASH:") for e in result.errors)


# 4. duplicate event id detected
def test_duplicate_event_id_detected():
    events = [
        {
            "id": "same-id",
            "operation_id": None,
            "parent_event_hash": "x",
            "event_type": "run_created",
            "worker_id": "kernel",
            "payload_json": '{"run_id":"r1"}',
            "artifact_hash": None,
            "event_hash": "h1",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "id": "same-id",
            "operation_id": None,
            "parent_event_hash": "h1",
            "event_type": "state_running",
            "worker_id": "kernel",
            "payload_json": '{"run_id":"r1"}',
            "artifact_hash": None,
            "event_hash": "h2",
            "created_at": "2026-01-01T00:00:01+00:00",
        },
    ]
    errors = auditor_module.check_duplicate_event_ids(events)
    assert any(e.startswith("DUPLICATE_EVENT_ID:same-id") for e in errors)


# 5. missing run_id detected
def test_missing_run_id_detected(ledger, lifecycle):
    # Bypass LifecycleKernel entirely: append a run-scoped event type
    # with no run_id in its payload at all.
    result = ledger.append_event(
        event_type="state_running", worker_id="kernel", payload={}
    )
    assert result.success

    audit_result = audit(ledger)
    assert not audit_result.success
    assert any(
        e.startswith(f"MISSING_RUN_ID:{result.event_id}") for e in audit_result.errors
    )


# 6. illegal lifecycle transition detected
def test_illegal_transition_detected(ledger, lifecycle):
    run_id = lifecycle.create_run("op-1").run_id
    # Skip state_running entirely -- go straight to artifact_written,
    # bypassing LifecycleKernel's own transition check by calling the
    # raw ledger API directly.
    result = ledger.append_event(
        event_type="artifact_written",
        worker_id="kernel",
        payload={"run_id": run_id, "artifact": {"type": "text", "content": "x"}},
    )
    assert result.success

    audit_result = audit(ledger)
    assert not audit_result.success
    assert any(
        e.startswith(f"ILLEGAL_TRANSITION:{result.event_id}")
        for e in audit_result.errors
    )


# 7. projection current_state mismatch detected
def test_projection_current_state_mismatch_detected(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    ledger.connection.execute(
        "UPDATE operation_state_projection SET current_state = 'state_completed' "
        "WHERE run_id = ?",
        (run_id,),
    )
    ledger.connection.commit()

    result = audit(ledger)
    assert not result.success
    assert not result.projections_valid
    assert any(e.startswith(f"PROJECTION_MISMATCH:{run_id}") for e in result.errors)


# 8. projection last_event_id mismatch detected
def test_projection_last_event_id_mismatch_detected(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    ledger.connection.execute(
        "UPDATE operation_state_projection SET last_event_id = 'bogus-event-id' "
        "WHERE run_id = ?",
        (run_id,),
    )
    ledger.connection.commit()

    result = audit(ledger)
    assert not result.success
    assert not result.projections_valid
    assert any(
        e.startswith(f"PROJECTION_LAST_EVENT_ID_MISMATCH:{run_id}")
        for e in result.errors
    )


# 9. projection updated_at mismatch detected
def test_projection_updated_at_mismatch_detected(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    ledger.connection.execute(
        "UPDATE operation_state_projection SET updated_at = '1999-01-01T00:00:00+00:00' "
        "WHERE run_id = ?",
        (run_id,),
    )
    ledger.connection.commit()

    result = audit(ledger)
    assert not result.success
    assert not result.projections_valid
    assert any(
        e.startswith(f"PROJECTION_UPDATED_AT_MISMATCH:{run_id}")
        for e in result.errors
    )


# 10. audit uses ledger created_at, not datetime.now()
def test_audit_uses_ledger_created_at_not_now(ledger, lifecycle, projection):
    import inspect

    source = inspect.getsource(auditor_module)
    assert "datetime.now" not in source
    assert "import datetime" not in source

    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    ledger_created_at = ledger.connection.execute(
        "SELECT created_at FROM ledger_events WHERE event_type = 'state_running' "
        "AND payload_json LIKE ?",
        (f'%"run_id":"{run_id}"%',),
    ).fetchone()[0]

    events = auditor_module._load_events(ledger)
    expected = auditor_module.compute_expected_projection(events)
    assert expected[run_id][2] == ledger_created_at


# 11 / 12 / 20. audit never mutates ledger, projections, or anything -- read-only
def test_audit_is_read_only(ledger, lifecycle, projection):
    _full_run(lifecycle)
    before = _snapshot(ledger)
    result = audit(ledger)
    after = _snapshot(ledger)

    assert before == after
    assert result.success


# 13. audit never calls rebuild_projection()
def test_audit_never_calls_rebuild_projection(ledger, lifecycle, projection, monkeypatch):
    import leira.projection.rebuild as rebuild_module

    def explode(*args, **kwargs):
        raise AssertionError("rebuild_projection() must never be called during audit")

    monkeypatch.setattr(rebuild_module, "rebuild_projection", explode)
    _full_run(lifecycle)

    # auditor.py doesn't even import the rebuild_projection function into
    # its own namespace, so it has no way to call it.
    assert not hasattr(auditor_module, "rebuild_projection")

    result = audit(ledger)
    assert result.success


# 14. audit deterministic
def test_audit_is_deterministic(ledger, lifecycle, projection):
    _full_run(lifecycle)
    first = audit(ledger)
    second = audit(ledger)
    assert first == second


# 15. error ordering stable
def test_error_ordering_stable(tmp_path):
    def build_and_audit():
        ledger = LedgerKernel(str(tmp_path / f"ledger_{uuid.uuid4()}.sqlite3"))
        projection = ProjectionEngine(ledger)
        lifecycle = LifecycleKernel(ledger, projection=projection)
        run_id = lifecycle.create_run("op-1").run_id
        ledger.append_event(
            event_type="artifact_written",
            worker_id="kernel",
            payload={"run_id": run_id, "artifact": {"type": "text", "content": "x"}},
        )
        result = audit(ledger)
        ledger.close()
        # Compare error *codes* in order, not the embedded run/event ids
        # -- those are random uuids and differ between independent runs
        # by construction, but the corruption shape and its ordering
        # must be identical every time.
        return [e.split(":", 1)[0] for e in result.errors]

    first = build_and_audit()
    second = build_and_audit()
    assert first == second
    assert first  # sanity: corruption was actually detected


# 16. multiple corruptions detected simultaneously
def test_multiple_corruptions_detected_simultaneously(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    # Corruption A: illegal transition (skip artifact_written).
    bad_transition = ledger.append_event(
        event_type="state_completed", worker_id="kernel", payload={"run_id": run_id}
    )

    # Corruption B: projection drift.
    ledger.connection.execute(
        "UPDATE operation_state_projection SET current_state = 'run_created' "
        "WHERE run_id = ?",
        (run_id,),
    )
    ledger.connection.commit()

    # Corruption C: a run-scoped event with no run_id at all.
    missing_run_id = ledger.append_event(
        event_type="state_running", worker_id="kernel", payload={}
    )

    result = audit(ledger)
    assert not result.success
    assert any(e.startswith(f"ILLEGAL_TRANSITION:{bad_transition.event_id}") for e in result.errors)
    assert any(e.startswith(f"PROJECTION_MISMATCH:{run_id}") for e in result.errors)
    assert any(e.startswith(f"MISSING_RUN_ID:{missing_run_id.event_id}") for e in result.errors)


# 17. artifact schema corruption detected
def test_artifact_schema_corruption_detected(ledger, lifecycle):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")
    result = ledger.append_event(
        event_type="artifact_written",
        worker_id="kernel",
        payload={"run_id": run_id, "artifact": {"type": "text"}},  # missing content
    )
    assert result.success

    audit_result = audit(ledger)
    assert not audit_result.success
    assert any(
        e.startswith(f"ARTIFACT_SCHEMA_INVALID:{result.event_id}")
        for e in audit_result.errors
    )


# 18. validate_chain() remains separate from audit
def test_validate_chain_remains_separate_from_audit(ledger, lifecycle):
    _full_run(lifecycle)

    chain_result = ledger.validate_chain()
    audit_result = audit(ledger)

    assert type(chain_result).__name__ == "ValidateChainResult"
    assert type(audit_result).__name__ == "AuditResult"
    assert chain_result.success
    assert audit_result.chain_valid == chain_result.success


# 19. projection disagreement favors ledger
def test_projection_disagreement_favors_ledger(ledger, lifecycle, projection):
    run_id = lifecycle.create_run("op-1").run_id
    lifecycle.append_lifecycle_event(run_id, "state_running")

    ledger.connection.execute(
        "UPDATE operation_state_projection SET current_state = 'state_completed' "
        "WHERE run_id = ?",
        (run_id,),
    )
    ledger.connection.commit()

    # The projection lies; the ledger-derived truth never does, and
    # audit() reports the lie without repairing it.
    assert projection.get_current_state(run_id) == "state_completed"
    assert lifecycle.get_run_state(run_id).current_state == "state_running"

    result = audit(ledger)
    assert any(e.startswith(f"PROJECTION_MISMATCH:{run_id}") for e in result.errors)
    # Nothing was repaired -- the lie is still there after auditing.
    assert projection.get_current_state(run_id) == "state_completed"
