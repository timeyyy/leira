import inspect
import json

import pytest

from leira.dispatcher.kernel import LedgerEvent, LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.dispatcher.dispatcher import dispatch_once
from leira.inbox.inbox import InboxKernel
from leira.projection.state import ProjectionEngine
from leira.workers.base import EchoWorker, FailingWorker
from leira.claims.claims import CLAIM_ESTABLISHED_EVENT_TYPE, ClaimKernel
from leira.receipts.receipts import (
    ReceiptBundle,
    export_receipt_bundle,
    get_receipt_bundle,
    list_receipt_events,
    rebuild_receipt_projection,
)
from leira.receipts import receipts as receipts_module
from leira.audit.auditor import audit


@pytest.fixture
def ledger(tmp_path):
    k = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    yield k
    k.close()


@pytest.fixture
def inbox(ledger):
    return InboxKernel(ledger)


@pytest.fixture
def lifecycle(ledger):
    return LifecycleKernel(ledger, projection=ProjectionEngine(ledger))


@pytest.fixture
def claims(ledger):
    return ClaimKernel(ledger)


def _submit(inbox, payload=None):
    result = inbox.submit_intent("worker", payload or {"message": "hi"})
    assert result.success
    return result.intent_id


def _receipt_projection_row(ledger, intent_id):
    return ledger.connection.execute(
        "SELECT intent_id, first_event_id, last_event_id, event_count, updated_at "
        "FROM receipt_projection WHERE intent_id = ?",
        (intent_id,),
    ).fetchone()


# 1. bundle created for completed intent
def test_bundle_created_for_completed_intent(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    bundle = get_receipt_bundle(ledger, intent_id)
    assert isinstance(bundle, ReceiptBundle)
    assert bundle.intent_id == intent_id
    assert "intent_completed" in [e.event_type for e in bundle.events]


# 2. bundle created for failed intent
def test_bundle_created_for_failed_intent(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, FailingWorker())
    bundle = get_receipt_bundle(ledger, intent_id)
    assert bundle is not None
    assert "intent_failed" in [e.event_type for e in bundle.events]


# 3. bundle created for rejected intent
def test_bundle_created_for_rejected_intent(ledger, inbox):
    intent_id = inbox.submit_intent("", {}).intent_id
    bundle = get_receipt_bundle(ledger, intent_id)
    assert bundle is not None
    assert bundle.events[0].event_type == "intent_rejected"


# 4. bundle created for in-progress intent
def test_bundle_created_for_in_progress_intent(ledger, inbox):
    intent_id = _submit(inbox)
    bundle = get_receipt_bundle(ledger, intent_id)
    assert bundle is not None
    assert bundle.events[-1].event_type == "intent_submitted"


# 5. rejected events included
def test_rejected_events_included(ledger, inbox):
    intent_id = inbox.submit_intent("", {}).intent_id
    events = list_receipt_events(ledger, intent_id)
    assert any(e.event_type == "intent_rejected" for e in events)


# 6. failure events included
def test_failure_events_included(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, FailingWorker())
    events = list_receipt_events(ledger, intent_id)
    assert any(e.event_type == "intent_failed" for e in events)


# 7. claim events included
def test_claim_events_included(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")
    events = list_receipt_events(ledger, intent_id)
    assert any(e.event_type == CLAIM_ESTABLISHED_EVENT_TYPE for e in events)


# 8. release events included
def test_release_events_included(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")
    claims.release_claim(intent_id, "owner-a")
    events = list_receipt_events(ledger, intent_id)
    assert any(e.event_type == "intent_released" for e in events)


# 9. first_event_id correct by ledger order
def test_first_event_id_correct_by_ledger_order(ledger, inbox):
    intent_id = _submit(inbox)
    row = ledger.connection.execute(
        "SELECT id FROM ledger_events WHERE event_type = 'intent_submitted' "
        "AND payload_json LIKE ? ORDER BY rowid LIMIT 1",
        (f'%"intent_id":"{intent_id}"%',),
    ).fetchone()
    bundle = get_receipt_bundle(ledger, intent_id)
    assert bundle.first_event_id == row[0]


# 10. last_event_id correct by ledger order
def test_last_event_id_correct_by_ledger_order(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    row = ledger.connection.execute(
        "SELECT id FROM ledger_events WHERE event_type = 'intent_completed' "
        "AND payload_json LIKE ? ORDER BY rowid DESC LIMIT 1",
        (f'%"intent_id":"{intent_id}"%',),
    ).fetchone()
    bundle = get_receipt_bundle(ledger, intent_id)
    assert bundle.last_event_id == row[0]


# 11. event_count equals len(events)
def test_event_count_equals_len_events(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    bundle = get_receipt_bundle(ledger, intent_id)
    assert bundle.event_count == len(bundle.events)


# 12. event_count equals ledger COUNT(*)
def test_event_count_equals_ledger_count(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    bundle = get_receipt_bundle(ledger, intent_id)

    direct_count = ledger.connection.execute(
        "SELECT COUNT(*) FROM ledger_events WHERE payload_json LIKE ?",
        (f'%"intent_id":"{intent_id}"%',),
    ).fetchone()[0]

    run_created_rows = ledger.connection.execute(
        "SELECT payload_json FROM ledger_events WHERE event_type = 'run_created' AND operation_id = ?",
        (intent_id,),
    ).fetchall()
    run_ids = {json.loads(r[0])["run_id"] for r in run_created_rows}

    bridged_count = 0
    for run_id in run_ids:
        bridged_count += ledger.connection.execute(
            "SELECT COUNT(*) FROM ledger_events WHERE payload_json LIKE ? AND event_type != 'run_created'",
            (f'%"run_id":"{run_id}"%',),
        ).fetchone()[0]

    assert bundle.event_count == direct_count + bridged_count


# 13. chronology preserved
def test_chronology_preserved(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    bundle = get_receipt_bundle(ledger, intent_id)
    event_types = [e.event_type for e in bundle.events]
    assert event_types == [
        "intent_submitted",
        "intent_claimed",
        "run_created",
        "state_running",
        "artifact_written",
        "intent_completed",
    ]


# 14. ledger ordering used instead of timestamps
def test_ledger_ordering_used_instead_of_timestamps(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    bundle = get_receipt_bundle(ledger, intent_id)

    rowids = [
        ledger.connection.execute(
            "SELECT rowid FROM ledger_events WHERE id = ?", (event.id,)
        ).fetchone()[0]
        for event in bundle.events
    ]
    assert rowids == sorted(rowids)

    source = inspect.getsource(receipts_module.list_receipt_events)
    assert "created_at" not in source


# 15. list_receipt_events queries ledger directly
def test_list_receipt_events_queries_ledger_directly():
    source = inspect.getsource(receipts_module.list_receipt_events)
    assert "receipt_projection" not in source
    assert "ledger_events" in source


# 16. receipt_projection is not source of events
def test_receipt_projection_is_not_source_of_events(ledger, inbox):
    intent_id = _submit(inbox)
    get_receipt_bundle(ledger, intent_id)  # populate the projection live

    ledger.connection.execute(
        "UPDATE receipt_projection SET event_count = 9999 WHERE intent_id = ?", (intent_id,)
    )
    ledger.connection.commit()

    events = list_receipt_events(ledger, intent_id)
    assert len(events) == 1  # the real count -- unaffected by the corrupted projection


# 17. export deterministic
def test_export_deterministic(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    export = export_receipt_bundle(ledger, intent_id)

    encoded_a = json.dumps(export, sort_keys=True, separators=(",", ":"))
    encoded_b = json.dumps(export, sort_keys=True, separators=(",", ":"))
    assert encoded_a == encoded_b


# 18. repeated export byte-identical
def test_repeated_export_byte_identical(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())

    first = export_receipt_bundle(ledger, intent_id)
    second = export_receipt_bundle(ledger, intent_id)

    first_json = json.dumps(first, sort_keys=True, separators=(",", ":"))
    second_json = json.dumps(second, sort_keys=True, separators=(",", ":"))
    assert first_json == second_json


# 19. projection rebuilt correctly
def test_projection_rebuilt_correctly(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    bundle = get_receipt_bundle(ledger, intent_id)

    ledger.connection.execute("DELETE FROM receipt_projection")
    ledger.connection.commit()
    assert _receipt_projection_row(ledger, intent_id) is None

    rebuild_receipt_projection(ledger)
    row = _receipt_projection_row(ledger, intent_id)
    assert row is not None
    assert row[3] == bundle.event_count


# 20. projection loss recoverable
def test_projection_loss_recoverable(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    get_receipt_bundle(ledger, intent_id)  # ensure the table exists at all

    ledger.connection.execute("DROP TABLE receipt_projection")
    ledger.connection.commit()

    bundle = get_receipt_bundle(ledger, intent_id)  # pure ledger read -- still works
    assert bundle is not None

    rebuild_receipt_projection(ledger)
    assert _receipt_projection_row(ledger, intent_id) is not None


# 21. updated_at derived from last event timestamp
def test_updated_at_derived_from_last_event_timestamp(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    bundle = get_receipt_bundle(ledger, intent_id)

    last_event_created_at = ledger.connection.execute(
        "SELECT created_at FROM ledger_events WHERE id = ?", (bundle.last_event_id,)
    ).fetchone()[0]

    row = _receipt_projection_row(ledger, intent_id)
    assert row[4] == last_event_created_at

    rebuild_receipt_projection(ledger)
    row_after_rebuild = _receipt_projection_row(ledger, intent_id)
    assert row_after_rebuild[4] == last_event_created_at

    source = inspect.getsource(receipts_module)
    assert "datetime.now" not in source


# 22. rebuild deterministic
def test_rebuild_deterministic(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())

    rebuild_receipt_projection(ledger)
    first = _receipt_projection_row(ledger, intent_id)
    rebuild_receipt_projection(ledger)
    second = _receipt_projection_row(ledger, intent_id)
    assert first == second


# 23. rebuild idempotent
def test_rebuild_idempotent(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())

    rebuild_receipt_projection(ledger)
    rows_first = ledger.connection.execute("SELECT * FROM receipt_projection").fetchall()
    rebuild_receipt_projection(ledger)
    rows_second = ledger.connection.execute("SELECT * FROM receipt_projection").fetchall()
    assert rows_first == rows_second


# 24. audit validates receipt projection
def test_audit_validates_receipt_projection(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    rebuild_receipt_projection(ledger)

    result = audit(ledger)
    assert result.success
    assert result.projections_valid


# 25. audit detects receipt event_count mismatch
def test_audit_detects_receipt_event_count_mismatch(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    get_receipt_bundle(ledger, intent_id)

    ledger.connection.execute(
        "UPDATE receipt_projection SET event_count = 9999 WHERE intent_id = ?", (intent_id,)
    )
    ledger.connection.commit()

    result = audit(ledger)
    assert not result.success
    assert any(e.startswith(f"RECEIPT_EVENT_COUNT_MISMATCH:{intent_id}") for e in result.errors)


# 26. audit detects first_event_id mismatch
def test_audit_detects_first_event_id_mismatch(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    get_receipt_bundle(ledger, intent_id)

    ledger.connection.execute(
        "UPDATE receipt_projection SET first_event_id = 'bogus' WHERE intent_id = ?", (intent_id,)
    )
    ledger.connection.commit()

    result = audit(ledger)
    assert not result.success
    assert any(e.startswith(f"RECEIPT_FIRST_EVENT_ID_MISMATCH:{intent_id}") for e in result.errors)


# 27. audit detects last_event_id mismatch
def test_audit_detects_last_event_id_mismatch(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    get_receipt_bundle(ledger, intent_id)

    ledger.connection.execute(
        "UPDATE receipt_projection SET last_event_id = 'bogus' WHERE intent_id = ?", (intent_id,)
    )
    ledger.connection.commit()

    result = audit(ledger)
    assert not result.success
    assert any(e.startswith(f"RECEIPT_LAST_EVENT_ID_MISMATCH:{intent_id}") for e in result.errors)


# 28. audit detects updated_at mismatch
def test_audit_detects_updated_at_mismatch(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    get_receipt_bundle(ledger, intent_id)

    ledger.connection.execute(
        "UPDATE receipt_projection SET updated_at = '1999-01-01T00:00:00+00:00' WHERE intent_id = ?",
        (intent_id,),
    )
    ledger.connection.commit()

    result = audit(ledger)
    assert not result.success
    assert any(e.startswith(f"RECEIPT_UPDATED_AT_MISMATCH:{intent_id}") for e in result.errors)


# 29. bundle does not omit failures
def test_bundle_does_not_omit_failures(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, FailingWorker())
    bundle = get_receipt_bundle(ledger, intent_id)
    event_types = [e.event_type for e in bundle.events]
    assert "intent_failed" in event_types
    assert "artifact_written" in event_types


# 30. bundle does not omit rejected events
def test_bundle_does_not_omit_rejected_events(ledger, inbox):
    intent_id = inbox.submit_intent("", {}).intent_id
    bundle = get_receipt_bundle(ledger, intent_id)
    assert any(e.event_type == "intent_rejected" for e in bundle.events)


# 31. bundle uses LedgerEvent dataclass
def test_bundle_uses_ledger_event_dataclass(ledger, inbox):
    intent_id = _submit(inbox)
    bundle = get_receipt_bundle(ledger, intent_id)
    assert all(isinstance(e, LedgerEvent) for e in bundle.events)


# 32. bundle uses ledger events only
def test_bundle_uses_ledger_events_only(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    bundle = get_receipt_bundle(ledger, intent_id)
    for event in bundle.events:
        row = ledger.connection.execute(
            "SELECT id FROM ledger_events WHERE id = ?", (event.id,)
        ).fetchone()
        assert row is not None


# 33. validate_chain() still succeeds
def test_validate_chain_still_succeeds(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    get_receipt_bundle(ledger, intent_id)
    rebuild_receipt_projection(ledger)
    assert ledger.validate_chain().success


# 34. audit remains read-only
def test_audit_remains_read_only(ledger, lifecycle, inbox):
    intent_id = _submit(inbox)
    dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
    rebuild_receipt_projection(ledger)

    before_ledger = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    before_proj = ledger.connection.execute(
        "SELECT * FROM receipt_projection ORDER BY intent_id"
    ).fetchall()

    audit(ledger)

    after_ledger = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    after_proj = ledger.connection.execute(
        "SELECT * FROM receipt_projection ORDER BY intent_id"
    ).fetchall()

    assert before_ledger == after_ledger
    assert before_proj == after_proj


# 35. 100-bundle stress test
def test_hundred_bundle_stress_test(ledger, lifecycle, inbox):
    intent_ids = []

    for i in range(25):
        intent_id = _submit(inbox, {"n": i})
        dispatch_once(ledger, lifecycle, intent_id, EchoWorker())
        intent_ids.append(intent_id)

    for i in range(25):
        intent_id = _submit(inbox, {"n": i})
        dispatch_once(ledger, lifecycle, intent_id, FailingWorker())
        intent_ids.append(intent_id)

    for i in range(25):
        intent_id = inbox.submit_intent("", {"n": i}).intent_id
        intent_ids.append(intent_id)

    for i in range(25):
        intent_id = _submit(inbox, {"n": i})  # left in-progress, never dispatched
        intent_ids.append(intent_id)

    assert len(intent_ids) == 100

    for intent_id in intent_ids:
        bundle = get_receipt_bundle(ledger, intent_id)
        assert bundle is not None
        assert bundle.event_count == len(bundle.events)

    rebuild_receipt_projection(ledger)
    rows = ledger.connection.execute("SELECT intent_id FROM receipt_projection").fetchall()
    assert {row[0] for row in rows} == set(intent_ids)

    assert audit(ledger).success
