import inspect

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.dispatcher.dispatcher import DispatchResult, dispatch_by_name
from leira.dispatcher import dispatcher as dispatcher_module
from leira.inbox.inbox import InboxKernel
from leira.projection.state import ProjectionEngine
from leira.workers.base import EchoWorker, FailingWorker, WorkerResult
from leira.registry.registry import RegisterResult, WorkerRegistry, rebuild_worker_projection
from leira.registry import registry as registry_module
from leira.audit.auditor import audit


@pytest.fixture
def ledger(tmp_path):
    k = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    yield k
    k.close()


@pytest.fixture
def registry(ledger):
    return WorkerRegistry(ledger)


@pytest.fixture
def lifecycle(ledger):
    return LifecycleKernel(ledger, projection=ProjectionEngine(ledger))


@pytest.fixture
def inbox(ledger):
    return InboxKernel(ledger)


def _ledger_event_types(ledger):
    return [
        r[0]
        for r in ledger.connection.execute(
            "SELECT event_type FROM ledger_events ORDER BY rowid"
        ).fetchall()
    ]


def _worker_projection_row(ledger, worker_name):
    return ledger.connection.execute(
        "SELECT worker_name, registered_at, last_event_id FROM worker_projection "
        "WHERE worker_name = ?",
        (worker_name,),
    ).fetchone()


class InvalidWorker:
    name = ""

    def invoke(self, inputs):
        return WorkerResult(success=True, outputs={})


class CountingWorker:
    instances_created = 0

    def __init__(self):
        type(self).instances_created += 1
        self.name = "CountingWorker"

    def invoke(self, inputs):
        return WorkerResult(success=True, outputs={})


class _StressWorker:
    def __init__(self, index):
        self.name = f"Worker{index:03d}"

    def invoke(self, inputs):
        return WorkerResult(success=True, outputs={})


# 1. worker registration succeeds
def test_worker_registration_succeeds(registry):
    result = registry.register_worker(EchoWorker())
    assert isinstance(result, RegisterResult)
    assert result.success
    assert result.worker_name == "EchoWorker"
    assert result.error_type is None


# 2. duplicate registration rejected
def test_duplicate_registration_rejected(registry):
    registry.register_worker(EchoWorker())
    second = registry.register_worker(EchoWorker())
    assert not second.success
    assert second.error_type == "DUPLICATE_WORKER"


# 3. invalid worker rejected
def test_invalid_worker_rejected(registry):
    result = registry.register_worker(InvalidWorker())
    assert not result.success
    assert result.error_type == "INVALID_WORKER"
    assert result.worker_name is None


# 4. unknown worker returns None
def test_unknown_worker_returns_none(registry):
    assert registry.get_worker("DoesNotExist") is None


# 5. exact lookup succeeds
def test_exact_lookup_succeeds(registry):
    worker = EchoWorker()
    registry.register_worker(worker)
    assert registry.get_worker("EchoWorker") is worker


# 6. wrong-case lookup fails
def test_wrong_case_lookup_fails(registry):
    registry.register_worker(EchoWorker())
    assert registry.get_worker("echoworker") is None
    assert registry.get_worker("ECHOWORKER") is None


# 7. list_workers deterministic
def test_list_workers_deterministic(registry):
    class WorkerB:
        name = "BWorker"

        def invoke(self, inputs):
            return WorkerResult(success=True, outputs={})

    class WorkerA:
        name = "AWorker"

        def invoke(self, inputs):
            return WorkerResult(success=True, outputs={})

    registry.register_worker(WorkerB())
    registry.register_worker(WorkerA())
    assert registry.list_workers() == ["AWorker", "BWorker"]
    assert registry.list_workers() == registry.list_workers()


# 8. worker_registered event recorded
def test_worker_registered_event_recorded(ledger, registry):
    registry.register_worker(EchoWorker())
    assert "worker_registered" in _ledger_event_types(ledger)


# 9. worker_registration_rejected event recorded
def test_worker_registration_rejected_event_recorded(ledger, registry):
    registry.register_worker(InvalidWorker())
    assert "worker_registration_rejected" in _ledger_event_types(ledger)


# 10. registry update happens only after successful ledger append
def test_registry_update_happens_only_after_successful_append(ledger, registry):
    original_append = ledger.append_event
    observed = {}

    def spy(*, event_type, worker_id, payload, **kwargs):
        if event_type == "worker_registered":
            observed["worker_in_dict_before_append_returns"] = "EchoWorker" in registry._workers
        return original_append(event_type=event_type, worker_id=worker_id, payload=payload, **kwargs)

    ledger.append_event = spy
    result = registry.register_worker(EchoWorker())

    assert result.success
    assert observed["worker_in_dict_before_append_returns"] is False
    assert "EchoWorker" in registry._workers


# 11. ledger append failure prevents in-memory registration
def test_ledger_append_failure_prevents_in_memory_registration(ledger, registry):
    ledger.connection.close()
    result = registry.register_worker(EchoWorker())
    assert not result.success
    assert result.error_type == "STORAGE_FAILURE"
    assert registry.get_worker("EchoWorker") is None


# 12. rejected registration never stores worker
def test_rejected_registration_never_stores_worker(registry):
    registry.register_worker(EchoWorker())
    duplicate_worker = EchoWorker()
    rejected = registry.register_worker(duplicate_worker)

    assert not rejected.success
    assert registry.get_worker("EchoWorker") is not duplicate_worker
    assert registry.list_workers().count("EchoWorker") == 1


# 13. projection rebuilt correctly
def test_projection_rebuilt_correctly(ledger, registry):
    registry.register_worker(EchoWorker())
    registry.register_worker(FailingWorker())

    ledger.connection.execute("DELETE FROM worker_projection")
    ledger.connection.commit()
    assert _worker_projection_row(ledger, "EchoWorker") is None

    rebuild_worker_projection(ledger)
    assert _worker_projection_row(ledger, "EchoWorker") is not None
    assert _worker_projection_row(ledger, "FailingWorker") is not None


# 14. registered_at comes from ledger timestamp
def test_registered_at_comes_from_ledger_timestamp(ledger, registry):
    registry.register_worker(EchoWorker())
    ledger_created_at = ledger.connection.execute(
        "SELECT created_at FROM ledger_events WHERE event_type = 'worker_registered'"
    ).fetchone()[0]

    row = _worker_projection_row(ledger, "EchoWorker")
    assert row[1] == ledger_created_at

    rebuild_worker_projection(ledger)
    row_after_rebuild = _worker_projection_row(ledger, "EchoWorker")
    assert row_after_rebuild[1] == ledger_created_at

    source = inspect.getsource(registry_module)
    assert "datetime.now" not in source


# 15. audit validates registry projection
def test_audit_validates_registry_projection(ledger, registry):
    registry.register_worker(EchoWorker())
    registry.register_worker(FailingWorker())
    result = audit(ledger)
    assert result.success
    assert result.projections_valid


# 16. audit detects duplicate registration history
def test_audit_detects_duplicate_registration_history(ledger, registry):
    registry.register_worker(EchoWorker())
    # The live registry's own dict can never produce this (it checks
    # itself before appending) -- simulate the one case that can: a
    # restarted process re-registering a name the ledger already saw.
    ledger.append_event(
        event_type="worker_registered",
        worker_id="kernel",
        payload={"worker_name": "EchoWorker", "status": "REGISTERED", "error_type": None},
    )

    result = audit(ledger)
    assert not result.success
    assert any(e.startswith("DUPLICATE_WORKER_REGISTRATION:EchoWorker") for e in result.errors)


# 17. dispatch_by_name delegates to dispatch_once
def test_dispatch_by_name_delegates_to_dispatch_once(ledger, lifecycle, inbox, registry):
    registry.register_worker(EchoWorker())
    intent_id = inbox.submit_intent("worker", {"message": "hi"}).intent_id

    result = dispatch_by_name(ledger, lifecycle, registry, intent_id, "EchoWorker")
    assert isinstance(result, DispatchResult)
    assert result.success
    assert result.status == "COMPLETED"
    assert result.worker_name == "EchoWorker"


# 18. dispatch_by_name returns UNKNOWN_WORKER for missing name
def test_dispatch_by_name_returns_unknown_worker_for_missing_name(ledger, lifecycle, inbox, registry):
    intent_id = inbox.submit_intent("worker", {"message": "hi"}).intent_id
    result = dispatch_by_name(ledger, lifecycle, registry, intent_id, "Nonexistent")
    assert not result.success
    assert result.error_type == "UNKNOWN_WORKER"


# 19. no routing logic introduced
def test_no_routing_logic_introduced():
    forbidden = ("fuzzy", "levenshtein", "priority", "weight", "confidence", "scheduler")
    registry_source = inspect.getsource(registry_module).lower()
    dispatch_source = inspect.getsource(dispatcher_module.dispatch_by_name).lower()
    for word in forbidden:
        assert word not in registry_source
        assert word not in dispatch_source


# 20. worker names are case-sensitive
def test_worker_names_are_case_sensitive(registry):
    class LowerWorker:
        name = "lowerworker"

        def invoke(self, inputs):
            return WorkerResult(success=True, outputs={})

    class UpperWorker:
        name = "LOWERWORKER"

        def invoke(self, inputs):
            return WorkerResult(success=True, outputs={})

    first = registry.register_worker(LowerWorker())
    second = registry.register_worker(UpperWorker())
    assert first.success
    assert second.success
    assert registry.get_worker("lowerworker") is not registry.get_worker("LOWERWORKER")


# 21. worker names immutable after registration
def test_worker_names_immutable_after_registration(registry):
    original = EchoWorker()
    registry.register_worker(original)

    replacement = EchoWorker()
    result = registry.register_worker(replacement)

    assert not result.success
    assert result.error_type == "DUPLICATE_WORKER"
    assert registry.get_worker("EchoWorker") is original


# 22. registry stores references only
def test_registry_stores_references_only(registry):
    worker = EchoWorker()
    registry.register_worker(worker)

    retrieved = registry.get_worker("EchoWorker")
    assert retrieved is worker

    worker.custom_marker = "still-the-same-object"
    assert registry.get_worker("EchoWorker").custom_marker == "still-the-same-object"


# 23. registry does not instantiate workers
def test_registry_does_not_instantiate_workers(registry):
    instance = CountingWorker()
    before = CountingWorker.instances_created

    registry.register_worker(instance)

    assert CountingWorker.instances_created == before
    assert registry.get_worker("CountingWorker") is instance


# 24. no dynamic imports or plugins exist
def test_no_dynamic_imports_or_plugins_exist():
    source = inspect.getsource(registry_module)
    for forbidden in ("importlib", "__import__", "exec(", "eval(", "pkgutil", "entry_points"):
        assert forbidden not in source


# 25. 100-worker stress test
def test_hundred_worker_stress_test(ledger, registry):
    workers = [_StressWorker(i) for i in range(100)]

    for worker in workers:
        result = registry.register_worker(worker)
        assert result.success

    assert registry.get_worker("Worker050") is workers[50]
    assert registry.get_worker("worker050") is None
    assert registry.list_workers() == sorted(w.name for w in workers)
    assert registry.list_workers() == registry.list_workers()

    rebuild_worker_projection(ledger)
    rows = ledger.connection.execute("SELECT worker_name FROM worker_projection").fetchall()
    assert {r[0] for r in rows} == {w.name for w in workers}

    audit_result = audit(ledger)
    assert audit_result.success


# 26. validate_chain() still succeeds
def test_validate_chain_still_succeeds(ledger, registry):
    registry.register_worker(EchoWorker())
    registry.register_worker(FailingWorker())
    registry.register_worker(InvalidWorker())

    result = ledger.validate_chain()
    assert result.success


# 27. typed failures preferred over exceptions
def test_typed_failures_preferred_over_exceptions(registry):
    class NoNameWorker:
        def invoke(self, inputs):
            return WorkerResult(success=True, outputs={})

    for worker in (InvalidWorker(), NoNameWorker()):
        result = registry.register_worker(worker)
        assert isinstance(result, RegisterResult)
        assert not result.success
        assert result.error_type is not None


# 28. projection loss recoverable
def test_projection_loss_recoverable(ledger, registry):
    registry.register_worker(EchoWorker())

    ledger.connection.execute("DROP TABLE worker_projection")
    ledger.connection.commit()

    # The registry itself never depended on the table.
    assert registry.get_worker("EchoWorker") is not None

    rebuild_worker_projection(ledger)
    assert _worker_projection_row(ledger, "EchoWorker") is not None


# 29. audit remains read-only
def test_audit_remains_read_only_for_registry(ledger, registry):
    registry.register_worker(EchoWorker())
    registry.register_worker(InvalidWorker())

    before_ledger = ledger.connection.execute(
        "SELECT * FROM ledger_events ORDER BY rowid"
    ).fetchall()
    before_proj = ledger.connection.execute(
        "SELECT * FROM worker_projection ORDER BY worker_name"
    ).fetchall()

    audit(ledger)

    after_ledger = ledger.connection.execute(
        "SELECT * FROM ledger_events ORDER BY rowid"
    ).fetchall()
    after_proj = ledger.connection.execute(
        "SELECT * FROM worker_projection ORDER BY worker_name"
    ).fetchall()

    assert before_ledger == after_ledger
    assert before_proj == after_proj


# 30. worker_projection does not imply Python object reconstruction
def test_worker_projection_does_not_imply_object_reconstruction(ledger, registry):
    registry.register_worker(EchoWorker())

    # A brand-new registry over the same ledger starts empty: the
    # projection remembers that "EchoWorker" was registered, but it
    # cannot, and does not, hand back a worker object.
    fresh_registry = WorkerRegistry(ledger)
    assert fresh_registry.get_worker("EchoWorker") is None
    assert fresh_registry.list_workers() == []

    rebuild_worker_projection(ledger)
    assert _worker_projection_row(ledger, "EchoWorker") is not None
    assert fresh_registry.get_worker("EchoWorker") is None
