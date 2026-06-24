import inspect

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.dispatcher.dispatcher import DispatchResult, dispatch_once
from leira.dispatcher import dispatcher as dispatcher_module
from leira.inbox.inbox import InboxKernel, get_intent_status, rebuild_intent_projection
from leira.projection.state import ProjectionEngine
from leira.workers.base import EchoWorker, ExplodingWorker, FailingWorker, WorkerResult
from leira.audit.auditor import audit


@pytest.fixture
def ledger(tmp_path):
    k = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    yield k
    k.close()


@pytest.fixture
def lifecycle(ledger):
    # dispatch_once creates real runs under the hood (state_running /
    # artifact_written); wiring a ProjectionEngine here keeps
    # operation_state_projection live, exactly as any other caller of
    # LifecycleKernel may choose to.
    return LifecycleKernel(ledger, projection=ProjectionEngine(ledger))


@pytest.fixture
def inbox(ledger):
    return InboxKernel(ledger)


def _submit(inbox, payload=None):
    result = inbox.submit_intent("worker", payload or {"message": "hi"})
    assert result.success
    return result.intent_id


def _projection_row(ledger, intent_id):
    return ledger.connection.execute(
        "SELECT intent_id, status, worker_name, updated_at, last_event_id "
        "FROM intent_projection WHERE intent_id = ?",
        (intent_id,),
    ).fetchone()


def _ledger_event_types(ledger):
    return [
        r[0]
        for r in ledger.connection.execute(
            "SELECT event_type FROM ledger_events ORDER BY rowid"
        ).fetchall()
    ]


class NamelessWorker:
    name = ""

    def invoke(self, inputs):
        return WorkerResult(success=True, outputs={})


# 1. pending intent dispatches successfully
def test_pending_intent_dispatches_successfully(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    result = dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert isinstance(result, DispatchResult)
    assert result.success
    assert result.status == "COMPLETED"
    assert result.intent_id == intent_id


# 2. unknown intent returns UNKNOWN_INTENT
def test_unknown_intent_returns_unknown_intent(ledger, lifecycle):
    result = dispatch_once(ledger, lifecycle, "does-not-exist", EchoWorker())
    assert not result.success
    assert result.error_type == "UNKNOWN_INTENT"


# 3. running intent rejected
def test_running_intent_rejected(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    # Force a second dispatch onto an intent already claimed: simulate
    # by directly checking the post-claim, pre-completion window using
    # a fresh intent and a worker that doesn't matter here -- instead,
    # directly verify status mid-flight via a manual claim.
    intent_id_2 = _submit(inbox)
    claimed = ledger.append_event(
        event_type="intent_claimed",
        worker_id="kernel",
        payload={"intent_id": intent_id_2, "status": "RUNNING", "worker_name": "X"},
    )
    assert claimed.success
    result = dispatch_once(ledger, lifecycle, intent_id_2, EchoWorker())
    assert not result.success
    assert result.status == "RUNNING"
    assert result.error_type == "INVALID_STATUS"


# 4. completed intent rejected
def test_completed_intent_rejected(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    first = dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert first.status == "COMPLETED"

    second = dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert not second.success
    assert second.status == "COMPLETED"
    assert second.error_type == "INVALID_STATUS"


# 5. failed intent rejected
def test_failed_intent_rejected(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    first = dispatch_once(ledger, lifecycle, intent_id, FailingWorker())
    assert first.status == "FAILED"

    second = dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert not second.success
    assert second.status == "FAILED"
    assert second.error_type == "INVALID_STATUS"


# 6. invalid worker name rejected
def test_invalid_worker_name_rejected(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    result = dispatch_once(ledger, lifecycle, intent_id, NamelessWorker())
    assert not result.success
    assert result.error_type == "INVALID_WORKER"
    # Nothing was claimed -- the intent is still PENDING.
    assert get_intent_status(ledger, intent_id) == "PENDING"


# 7. worker success produces COMPLETED
def test_worker_success_produces_completed(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    result = dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert result.status == "COMPLETED"
    assert get_intent_status(ledger, intent_id) == "COMPLETED"


# 8. worker failure produces FAILED
def test_worker_failure_produces_failed(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    result = dispatch_once(ledger, lifecycle, intent_id, FailingWorker())
    assert result.success  # dispatcher did its job; the worker failed
    assert result.status == "FAILED"
    assert get_intent_status(ledger, intent_id) == "FAILED"


# 9. worker exception becomes UNEXPECTED and FAILED
def test_worker_exception_becomes_unexpected_and_failed(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    result = dispatch_once(ledger, lifecycle, intent_id, ExplodingWorker())
    assert result.success
    assert result.status == "FAILED"

    row = ledger.connection.execute(
        "SELECT payload_json FROM ledger_events WHERE event_type = 'intent_failed'"
    ).fetchone()
    import json

    payload = json.loads(row[0])
    assert payload["error_type"] == "UNEXPECTED"


# 10. intent_claimed event recorded
def test_intent_claimed_event_recorded(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert "intent_claimed" in _ledger_event_types(ledger)


# 11. state_running event recorded
def test_state_running_event_recorded(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert "state_running" in _ledger_event_types(ledger)


# 12. artifact_written event recorded
def test_artifact_written_event_recorded(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert "artifact_written" in _ledger_event_types(ledger)


# 13. intent_completed event recorded on success
def test_intent_completed_event_recorded_on_success(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert "intent_completed" in _ledger_event_types(ledger)
    assert "intent_failed" not in _ledger_event_types(ledger)


# 14. intent_failed event recorded on failure
def test_intent_failed_event_recorded_on_failure(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, FailingWorker())
    assert "intent_failed" in _ledger_event_types(ledger)
    assert "intent_completed" not in _ledger_event_types(ledger)


# 15. worker_name preserved
def test_worker_name_preserved(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    result = dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert result.worker_name == "EchoWorker"

    artifact_row = ledger.connection.execute(
        "SELECT payload_json FROM ledger_events WHERE event_type = 'artifact_written'"
    ).fetchone()
    import json

    artifact_payload = json.loads(artifact_row[0])
    assert artifact_payload["artifact"]["content"]["worker_name"] == "EchoWorker"


# 16. worker_name is provenance only, not routing
def test_worker_name_is_provenance_only():
    # Check the actual implementation, not the module's prose docstring
    # (which discusses, in passing, all the things deliberately absent).
    source = inspect.getsource(dispatcher_module.dispatch_once)
    for forbidden in ("registry", "lookup_worker", "WORKER_REGISTRY", "route"):
        assert forbidden not in source


# 17. double dispatch prevented
def test_double_dispatch_prevented(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    first = dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert first.success

    second = dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert not second.success
    assert second.error_type == "INVALID_STATUS"

    # Only one claim, one completion ever recorded.
    event_types = _ledger_event_types(ledger)
    assert event_types.count("intent_claimed") == 1
    assert event_types.count("intent_completed") == 1


# 18. terminal states immutable
def test_terminal_states_immutable(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    assert get_intent_status(ledger, intent_id) == "COMPLETED"

    for _ in range(3):
        result = dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
        assert not result.success
        assert result.error_type == "INVALID_STATUS"
    assert get_intent_status(ledger, intent_id) == "COMPLETED"


# 19. projection rebuilt successfully
def test_projection_rebuilt_successfully(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())

    ledger.connection.execute("DELETE FROM intent_projection")
    ledger.connection.commit()
    assert _projection_row(ledger, intent_id) is None

    rebuild_intent_projection(ledger)
    row = _projection_row(ledger, intent_id)
    assert row is not None
    assert row[1] == "COMPLETED"
    assert row[2] == "EchoWorker"


# 20. projection uses ledger timestamps
def test_projection_uses_ledger_timestamps(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    rebuild_intent_projection(ledger)

    ledger_created_at = ledger.connection.execute(
        "SELECT created_at FROM ledger_events WHERE event_type = 'intent_completed'"
    ).fetchone()[0]
    row = _projection_row(ledger, intent_id)
    assert row[3] == ledger_created_at

    source = inspect.getsource(dispatcher_module)
    assert "datetime.now" not in source


# 21. projection preserves last legal terminal state
def test_projection_preserves_last_legal_terminal_state(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())

    # Bypass the dispatcher and tamper with history: append an illegal
    # event after the terminal intent_completed.
    ledger.append_event(
        event_type="intent_claimed",
        worker_id="kernel",
        payload={"intent_id": intent_id, "status": "RUNNING", "worker_name": "Intruder"},
    )

    rebuild_intent_projection(ledger)
    row = _projection_row(ledger, intent_id)
    assert row[1] == "COMPLETED"
    assert row[2] == "EchoWorker"


# 22. audit detects duplicate claims
def test_audit_detects_duplicate_claims(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    ledger.append_event(
        event_type="intent_claimed",
        worker_id="kernel",
        payload={"intent_id": intent_id, "status": "RUNNING", "worker_name": "A"},
    )
    ledger.append_event(
        event_type="intent_claimed",
        worker_id="kernel",
        payload={"intent_id": intent_id, "status": "RUNNING", "worker_name": "B"},
    )

    result = audit(ledger)
    assert not result.success
    assert any(e.startswith(f"DUPLICATE_CLAIM:{intent_id}") for e in result.errors)


# 23. audit detects transition after terminal state
def test_audit_detects_transition_after_terminal_state(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())

    bogus = ledger.append_event(
        event_type="intent_claimed",
        worker_id="kernel",
        payload={"intent_id": intent_id, "status": "RUNNING", "worker_name": "Intruder"},
    )

    result = audit(ledger)
    assert not result.success
    assert any(e.startswith(f"ILLEGAL_TRANSITION:{bogus.event_id}") for e in result.errors)


# 24. audit validates execution history
def test_audit_validates_execution_history(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    result = audit(ledger)
    assert result.success
    assert result.chain_valid
    assert result.projections_valid


# 25. dispatch_once contains no internal loop
def test_dispatch_once_contains_no_internal_loop():
    source = inspect.getsource(dispatcher_module.dispatch_once)
    assert "for " not in source
    assert "while " not in source


# 26 / 27. caller must dispatch by calling dispatch_once repeatedly; 100 sequential calls pass
def test_hundred_sequential_dispatch_calls_pass(ledger, lifecycle, inbox):
    intent_ids = [_submit(inbox, {"n": i}) for i in range(100)]

    results = []
    for intent_id in intent_ids:
        # The caller's own loop -- not inside dispatch_once.
        results.append(dispatch_once(ledger, lifecycle, intent_id, EchoWorker()))

    assert all(r.success and r.status == "COMPLETED" for r in results)
    assert ledger.validate_chain().success

    audit_result = audit(ledger)
    assert audit_result.success


# 28. validate_chain() still succeeds
def test_validate_chain_still_succeeds(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    intent_id_2 = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id_2, FailingWorker())

    result = ledger.validate_chain()
    assert result.success


# 29. typed failures preferred over exceptions
def test_typed_failures_preferred_over_exceptions(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    for bad_intent_id, worker in [
        ("nonexistent", EchoWorker()),
        (intent_id, NamelessWorker()),
    ]:
        result = dispatch_once(ledger, lifecycle, bad_intent_id, worker)
        assert isinstance(result, DispatchResult)
        assert not result.success
        assert result.error_type is not None


# 30. dispatcher contains no routing logic
def test_dispatcher_contains_no_routing_logic():
    source = inspect.getsource(dispatcher_module.dispatch_once)
    forbidden = ("route", "scheduler", "priority", "retry", "registry")
    lowered = source.lower()
    for word in forbidden:
        assert word not in lowered
