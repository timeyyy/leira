import json

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import (
    InboxKernel,
    IntentEnvelope,
    SubmitIntentResult,
    rebuild_intent_projection,
)
from leira.audit.auditor import audit


@pytest.fixture
def ledger(tmp_path):
    k = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    yield k
    k.close()


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


def _inbox_row(ledger, intent_id):
    return ledger.connection.execute(
        "SELECT intent_id, created_at, intent_type, payload_json, status "
        "FROM inbox_entries WHERE intent_id = ?",
        (intent_id,),
    ).fetchone()


def _projection_row(ledger, intent_id):
    return ledger.connection.execute(
        "SELECT intent_id, status, updated_at, last_event_id "
        "FROM intent_projection WHERE intent_id = ?",
        (intent_id,),
    ).fetchone()


# 1 / 2. valid intent accepted, returns status=PENDING
def test_valid_intent_accepted(inbox):
    result = inbox.submit_intent("shell_command", {"command": ["python", "--version"]})
    assert isinstance(result, SubmitIntentResult)
    assert result.success
    assert result.status == "PENDING"
    assert result.intent_id is not None
    assert result.error_type is None


# 3 / 4. invalid intent rejected, returns status=REJECTED
def test_invalid_intent_rejected(inbox):
    result = inbox.submit_intent("", {"ok": True})
    assert not result.success
    assert result.status == "REJECTED"
    assert result.intent_id is not None
    assert result.error_type == "INVALID_ENVELOPE"


# 5. non-serializable payload rejected
def test_non_serializable_payload_rejected(inbox):
    result = inbox.submit_intent("shell_command", {"value": float("nan")})
    assert not result.success
    assert result.status == "REJECTED"
    assert result.error_type == "NON_SERIALIZABLE_PAYLOAD"


# 6. empty intent_type rejected
def test_empty_intent_type_rejected(inbox):
    result = inbox.submit_intent("", {"a": 1})
    assert not result.success
    assert result.error_type == "INVALID_ENVELOPE"


# 7. non-string intent_type rejected
def test_non_string_intent_type_rejected(inbox):
    result = inbox.submit_intent(123, {"a": 1})
    assert not result.success
    assert result.error_type == "INVALID_ENVELOPE"


# 8. non-dict payload rejected
def test_non_dict_payload_rejected(inbox):
    result = inbox.submit_intent("shell_command", ["not", "a", "dict"])
    assert not result.success
    assert result.error_type == "INVALID_ENVELOPE"


# 9. intent_submitted event appended
def test_intent_submitted_event_appended(ledger, inbox):
    inbox.submit_intent("worker", {"worker_name": "EchoWorker", "inputs": {}})
    assert _ledger_event_types(ledger) == ["intent_submitted"]


# 10. intent_rejected event appended
def test_intent_rejected_event_appended(ledger, inbox):
    inbox.submit_intent("", {"a": 1})
    assert _ledger_event_types(ledger) == ["intent_rejected"]


# 11. inbox row created for accepted intent
def test_inbox_row_created_for_accepted_intent(ledger, inbox):
    result = inbox.submit_intent("shell_command", {"command": ["echo", "hi"]})
    row = _inbox_row(ledger, result.intent_id)
    assert row is not None
    assert row[4] == "PENDING"


# 12. inbox row created for rejected intent
def test_inbox_row_created_for_rejected_intent(ledger, inbox):
    result = inbox.submit_intent("", {"a": 1})
    row = _inbox_row(ledger, result.intent_id)
    assert row is not None
    assert row[4] == "REJECTED"


# 13. same intent_id appears in ledger, inbox_entries, and intent_projection
def test_same_intent_id_across_all_three_layers(ledger, inbox):
    result = inbox.submit_intent("shell_command", {"command": ["echo", "hi"]})
    intent_id = result.intent_id

    ledger_row = ledger.connection.execute(
        "SELECT payload_json FROM ledger_events WHERE event_type = 'intent_submitted'"
    ).fetchone()
    ledger_payload = json.loads(ledger_row[0])
    assert ledger_payload["intent_id"] == intent_id

    assert _inbox_row(ledger, intent_id)[0] == intent_id
    assert _projection_row(ledger, intent_id)[0] == intent_id


# 14. payload preserved exactly
def test_payload_preserved_exactly(ledger, inbox):
    payload = {"command": ["python", "--version"], "nested": {"a": [1, 2, 3]}}
    result = inbox.submit_intent("shell_command", payload)

    row = _inbox_row(ledger, result.intent_id)
    stored_payload = json.loads(row[3])
    assert stored_payload == payload

    ledger_row = ledger.connection.execute(
        "SELECT payload_json FROM ledger_events WHERE event_type = 'intent_submitted'"
    ).fetchone()
    assert json.loads(ledger_row[0])["payload"] == payload


# 15. stored payload JSON is deterministic
def test_stored_payload_json_is_deterministic(ledger, inbox):
    payload = {"b": 2, "a": 1}
    result_1 = inbox.submit_intent("shell_command", payload)
    result_2 = inbox.submit_intent("shell_command", payload)

    row_1 = _inbox_row(ledger, result_1.intent_id)
    row_2 = _inbox_row(ledger, result_2.intent_id)
    assert row_1[3] == row_2[3]
    assert row_1[3] == '{"a":1,"b":2}'


# 16. intent ids are unique
def test_intent_ids_are_unique(inbox):
    ids = {inbox.submit_intent("shell_command", {"n": i}).intent_id for i in range(20)}
    assert len(ids) == 20


# 17. multiple intents coexist
def test_multiple_intents_coexist(ledger, inbox):
    r1 = inbox.submit_intent("shell_command", {"n": 1})
    r2 = inbox.submit_intent("worker", {"n": 2})
    assert _inbox_row(ledger, r1.intent_id) is not None
    assert _inbox_row(ledger, r2.intent_id) is not None
    assert r1.intent_id != r2.intent_id


# 18. no intent executes automatically
def test_no_intent_executes_automatically(ledger, inbox):
    inbox.submit_intent("shell_command", {"command": ["python", "--version"]})
    event_types = set(_ledger_event_types(ledger))
    # Only ingress events exist -- nothing resembling execution/lifecycle.
    assert event_types <= {"intent_submitted", "intent_rejected"}
    assert "state_running" not in event_types
    assert "state_completed" not in event_types
    assert "artifact_written" not in event_types


# 19. projection loss loses no truth
def test_projection_loss_loses_no_truth(ledger, inbox):
    result = inbox.submit_intent("shell_command", {"n": 1})
    ledger.connection.execute("DELETE FROM intent_projection")
    ledger.connection.commit()

    assert _projection_row(ledger, result.intent_id) is None
    # The ledger and inbox row are untouched.
    assert _inbox_row(ledger, result.intent_id) is not None
    assert ledger.validate_chain().success


# 20. rebuild restores intent_projection
def test_rebuild_restores_intent_projection(ledger, inbox):
    result = inbox.submit_intent("shell_command", {"n": 1})
    ledger.connection.execute("DELETE FROM intent_projection")
    ledger.connection.commit()
    assert _projection_row(ledger, result.intent_id) is None

    rebuild_intent_projection(ledger)
    row = _projection_row(ledger, result.intent_id)
    assert row is not None
    assert row[1] == "PENDING"


# 21. rebuild is deterministic and idempotent
def test_rebuild_is_deterministic_and_idempotent(ledger, inbox):
    inbox.submit_intent("shell_command", {"n": 1})
    inbox.submit_intent("", {"n": 2})

    rebuild_intent_projection(ledger)
    first = ledger.connection.execute(
        "SELECT * FROM intent_projection ORDER BY intent_id"
    ).fetchall()
    rebuild_intent_projection(ledger)
    second = ledger.connection.execute(
        "SELECT * FROM intent_projection ORDER BY intent_id"
    ).fetchall()
    assert first == second
    assert len(first) == 2


# 22. updated_at comes from ledger timestamp
def test_updated_at_comes_from_ledger_timestamp(ledger, inbox):
    result = inbox.submit_intent("shell_command", {"n": 1})
    ledger_created_at = ledger.connection.execute(
        "SELECT created_at FROM ledger_events WHERE event_type = 'intent_submitted'"
    ).fetchone()[0]
    proj_row = _projection_row(ledger, result.intent_id)
    assert proj_row[2] == ledger_created_at


# 23. last_event_id comes from ledger event id
def test_last_event_id_comes_from_ledger_event_id(ledger, inbox):
    result = inbox.submit_intent("shell_command", {"n": 1})
    ledger_event_id = ledger.connection.execute(
        "SELECT id FROM ledger_events WHERE event_type = 'intent_submitted'"
    ).fetchone()[0]
    proj_row = _projection_row(ledger, result.intent_id)
    assert proj_row[3] == ledger_event_id


# 24. audit detects projection corruption
def test_audit_detects_intent_projection_corruption(ledger, inbox):
    result = inbox.submit_intent("shell_command", {"n": 1})
    ledger.connection.execute(
        "UPDATE intent_projection SET status = 'bogus' WHERE intent_id = ?",
        (result.intent_id,),
    )
    ledger.connection.commit()

    audit_result = audit(ledger)
    assert not audit_result.success
    assert not audit_result.projections_valid
    assert any(
        e.startswith(f"INTENT_PROJECTION_MISMATCH:{result.intent_id}")
        for e in audit_result.errors
    )


# 25. audit detects inbox/projection disagreement
def test_audit_detects_inbox_disagreement(ledger, inbox):
    result = inbox.submit_intent("shell_command", {"n": 1})
    ledger.connection.execute(
        "UPDATE inbox_entries SET status = 'REJECTED' WHERE intent_id = ?",
        (result.intent_id,),
    )
    ledger.connection.commit()

    audit_result = audit(ledger)
    assert not audit_result.success
    assert any(
        e.startswith(f"INTENT_STATUS_MISMATCH:{result.intent_id}")
        for e in audit_result.errors
    )


# 26. audit remains read-only
def test_audit_is_read_only_for_intents(ledger, inbox):
    inbox.submit_intent("shell_command", {"n": 1})
    inbox.submit_intent("", {"n": 2})

    before_ledger = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    before_inbox = ledger.connection.execute("SELECT * FROM inbox_entries ORDER BY intent_id").fetchall()
    before_proj = ledger.connection.execute("SELECT * FROM intent_projection ORDER BY intent_id").fetchall()

    audit(ledger)

    after_ledger = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    after_inbox = ledger.connection.execute("SELECT * FROM inbox_entries ORDER BY intent_id").fetchall()
    after_proj = ledger.connection.execute("SELECT * FROM intent_projection ORDER BY intent_id").fetchall()

    assert before_ledger == after_ledger
    assert before_inbox == after_inbox
    assert before_proj == after_proj


# 27. typed failures preferred over exceptions
def test_typed_failures_preferred_over_exceptions(inbox):
    for intent_type, payload in [
        (123, {"a": 1}),
        ("", {"a": 1}),
        ("shell_command", "not-a-dict"),
        ("shell_command", {"v": float("inf")}),
    ]:
        result = inbox.submit_intent(intent_type, payload)
        assert isinstance(result, SubmitIntentResult)
        assert not result.success
        assert result.error_type is not None


# 28. validate_chain() remains valid
def test_validate_chain_remains_valid(ledger, inbox):
    inbox.submit_intent("shell_command", {"n": 1})
    inbox.submit_intent("", {"n": 2})
    inbox.submit_intent("worker", {"worker_name": "EchoWorker"})

    result = ledger.validate_chain()
    assert result.success
    assert result.events_checked == 3


# 29. 100-intent stress test
def test_hundred_intent_stress_test(ledger, inbox):
    intent_ids = []
    for i in range(100):
        result = inbox.submit_intent("shell_command", {"n": i})
        assert result.success
        intent_ids.append(result.intent_id)

    assert len(set(intent_ids)) == 100
    assert ledger.validate_chain().success

    clean_audit = audit(ledger)
    assert clean_audit.success

    ledger.connection.execute("DELETE FROM intent_projection")
    ledger.connection.commit()

    corrupted_audit = audit(ledger)
    assert not corrupted_audit.success
    assert not corrupted_audit.projections_valid
    assert len(corrupted_audit.errors) >= 100

    rebuild_intent_projection(ledger)

    restored_audit = audit(ledger)
    assert restored_audit.success
    assert restored_audit.projections_valid

    for intent_id in intent_ids:
        row = _projection_row(ledger, intent_id)
        assert row is not None
        assert row[1] == "PENDING"


# 30. no reaper or cleaner exists
def test_no_reaper_or_cleaner_exists():
    import leira.inbox.inbox as inbox_module

    source_names = dir(inbox_module)
    forbidden_substrings = ("reap", "clean", "stale", "expire", "purge")
    for name in source_names:
        lowered = name.lower()
        assert not any(bad in lowered for bad in forbidden_substrings), name
