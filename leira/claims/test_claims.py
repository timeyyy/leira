import inspect
import json
import re
import uuid

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.dispatcher.dispatcher import dispatch_and_track
from leira.inbox.inbox import InboxKernel, get_intent_status
from leira.projection.state import ProjectionEngine
from leira.workers.base import EchoWorker
from leira.claims.claims import (
    CLAIM_ESTABLISHED_EVENT_TYPE,
    CLAIM_RELEASED_EVENT_TYPE,
    ClaimKernel,
    ClaimResult,
    ReleaseResult,
    get_claim,
    rebuild_claim_projection,
)
from leira.claims import claims as claims_module
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
def claims(ledger):
    return ClaimKernel(ledger)


@pytest.fixture
def lifecycle(ledger):
    return LifecycleKernel(ledger, projection=ProjectionEngine(ledger))


def _submit(inbox, payload=None):
    result = inbox.submit_intent("worker", payload or {"message": "hi"})
    assert result.success
    return result.intent_id


def _ledger_event_types(ledger):
    return [
        r[0]
        for r in ledger.connection.execute(
            "SELECT event_type FROM ledger_events ORDER BY rowid"
        ).fetchall()
    ]


def _claim_projection_row(ledger, intent_id):
    return ledger.connection.execute(
        "SELECT intent_id, claim_id, owner_id, claimed_at, last_event_id "
        "FROM intent_claim_projection WHERE intent_id = ?",
        (intent_id,),
    ).fetchone()


# 1. claim succeeds for PENDING intent
def test_claim_succeeds_for_pending_intent(claims, inbox):
    intent_id = _submit(inbox)
    result = claims.claim_intent(intent_id, "owner-a")
    assert isinstance(result, ClaimResult)
    assert result.success
    assert result.claim_id is not None
    assert result.error_type is None


# 2. claim rejected for unknown intent
def test_claim_rejected_for_unknown_intent(claims):
    result = claims.claim_intent("does-not-exist", "owner-a")
    assert not result.success
    assert result.error_type == "UNKNOWN_INTENT"


# 3. claim rejected for invalid owner
def test_claim_rejected_for_invalid_owner(claims, inbox):
    intent_id = _submit(inbox)
    result = claims.claim_intent(intent_id, "")
    assert not result.success
    assert result.error_type == "INVALID_OWNER"


# 4. claim rejected for non-PENDING intent
def test_claim_rejected_for_non_pending_intent(claims, inbox):
    intent_id = inbox.submit_intent("", {}).intent_id  # structurally invalid -> REJECTED
    result = claims.claim_intent(intent_id, "owner-a")
    assert not result.success
    assert result.error_type == "INVALID_STATUS"


# 5. duplicate claim rejected
def test_duplicate_claim_rejected(claims, inbox):
    intent_id = _submit(inbox)
    first = claims.claim_intent(intent_id, "owner-a")
    assert first.success
    second = claims.claim_intent(intent_id, "owner-b")
    assert not second.success
    assert second.error_type == "ALREADY_CLAIMED"


# 6. release succeeds for matching owner
def test_release_succeeds_for_matching_owner(claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")
    result = claims.release_claim(intent_id, "owner-a")
    assert isinstance(result, ReleaseResult)
    assert result.success
    assert result.error_type is None


# 7. release rejected when no active claim exists
def test_release_rejected_when_no_active_claim(claims, inbox):
    intent_id = _submit(inbox)
    result = claims.release_claim(intent_id, "owner-a")
    assert not result.success
    assert result.error_type == "NO_ACTIVE_CLAIM"


# 8. release rejected for wrong owner
def test_release_rejected_for_wrong_owner(claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")
    result = claims.release_claim(intent_id, "owner-b")
    assert not result.success
    assert result.error_type == "OWNER_MISMATCH"


# 9. claim-established event recorded
#
# Recorded as CLAIM_ESTABLISHED_EVENT_TYPE ("intent_claim_established"),
# not the literal "intent_claimed" -- that name already belongs to
# v1.0's own, unrelated intent-execution event. See claims.py's module
# docstring ("A deliberate naming deviation").
def test_claim_established_event_recorded(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")
    assert CLAIM_ESTABLISHED_EVENT_TYPE in _ledger_event_types(ledger)


# 10. intent_released event recorded
def test_intent_released_event_recorded(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")
    claims.release_claim(intent_id, "owner-a")
    assert "intent_released" in _ledger_event_types(ledger)


# 11. intent_claim_rejected event recorded
def test_intent_claim_rejected_event_recorded(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "")
    assert "intent_claim_rejected" in _ledger_event_types(ledger)


# 12. intent_release_rejected event recorded
def test_intent_release_rejected_event_recorded(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.release_claim(intent_id, "owner-a")
    assert "intent_release_rejected" in _ledger_event_types(ledger)


# 13. claim artifact recorded
def test_claim_artifact_recorded(ledger, claims, inbox):
    intent_id = _submit(inbox)
    result = claims.claim_intent(intent_id, "owner-a")

    row = ledger.connection.execute(
        "SELECT payload_json FROM ledger_events WHERE event_type = ?",
        (CLAIM_ESTABLISHED_EVENT_TYPE,),
    ).fetchone()
    artifact = json.loads(row[0])["artifact"]

    assert artifact["type"] == "claim"
    assert artifact["content"]["claim_id"] == result.claim_id
    assert artifact["content"]["intent_id"] == intent_id
    assert artifact["content"]["owner_id"] == "owner-a"
    assert artifact["content"]["action"] == "CLAIMED"
    assert artifact["content"]["error_type"] is None


# 14. rejected claim artifact recorded
def test_rejected_claim_artifact_recorded(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "")

    row = ledger.connection.execute(
        "SELECT payload_json FROM ledger_events WHERE event_type = 'intent_claim_rejected'"
    ).fetchone()
    artifact = json.loads(row[0])["artifact"]

    assert artifact["type"] == "claim"
    assert artifact["content"]["claim_id"] is None
    assert artifact["content"]["action"] == "REJECTED"
    assert artifact["content"]["error_type"] == "INVALID_OWNER"


# 15. projection rebuilt correctly
def test_projection_rebuilt_correctly(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")

    ledger.connection.execute("DELETE FROM intent_claim_projection")
    ledger.connection.commit()
    assert _claim_projection_row(ledger, intent_id) is None

    rebuild_claim_projection(ledger)
    row = _claim_projection_row(ledger, intent_id)
    assert row is not None
    assert row[2] == "owner-a"


# 16. claimed_at derived from ledger timestamp
def test_claimed_at_derived_from_ledger_timestamp(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")

    ledger_created_at = ledger.connection.execute(
        "SELECT created_at FROM ledger_events WHERE event_type = ?",
        (CLAIM_ESTABLISHED_EVENT_TYPE,),
    ).fetchone()[0]

    row = _claim_projection_row(ledger, intent_id)
    assert row[3] == ledger_created_at

    rebuild_claim_projection(ledger)
    row_after_rebuild = _claim_projection_row(ledger, intent_id)
    assert row_after_rebuild[3] == ledger_created_at

    source = inspect.getsource(claims_module)
    assert "datetime.now" not in source


# 17. released claims disappear from projection
def test_released_claims_disappear_from_projection(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")
    assert _claim_projection_row(ledger, intent_id) is not None

    claims.release_claim(intent_id, "owner-a")
    assert _claim_projection_row(ledger, intent_id) is None

    rebuild_claim_projection(ledger)
    assert _claim_projection_row(ledger, intent_id) is None


# 18. orphaned claims remain visible
def test_orphaned_claims_remain_visible(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")
    # No release call -- simulates a crashed owner.

    info = get_claim(ledger, intent_id)
    assert info is not None
    assert info.owner_id == "owner-a"

    rebuild_claim_projection(ledger)
    assert _claim_projection_row(ledger, intent_id) is not None

    result = audit(ledger)
    assert result.success  # an orphan is visible state, not corruption


# 19. duplicate active claims detected by audit
def test_duplicate_active_claims_detected_by_audit(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")
    # Bypass the kernel to simulate illegal history: a second claim
    # established while the first is still active.
    ledger.append_event(
        event_type=CLAIM_ESTABLISHED_EVENT_TYPE,
        worker_id="kernel",
        payload={"claim_id": str(uuid.uuid4()), "intent_id": intent_id, "owner_id": "owner-b"},
    )

    result = audit(ledger)
    assert not result.success
    assert any(e.startswith("DUPLICATE_ACTIVE_CLAIM:") for e in result.errors)


# 20. release owner mismatch detected by audit
def test_release_owner_mismatch_detected_by_audit(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claim_result = claims.claim_intent(intent_id, "owner-a")
    # Bypass the kernel: a release event with a mismatched owner_id.
    ledger.append_event(
        event_type=CLAIM_RELEASED_EVENT_TYPE,
        worker_id="kernel",
        payload={"claim_id": claim_result.claim_id, "intent_id": intent_id, "owner_id": "owner-b"},
    )

    result = audit(ledger)
    assert not result.success
    assert any(e.startswith("RELEASE_OWNER_MISMATCH:") for e in result.errors)


# 21. projection loss recoverable
def test_projection_loss_recoverable(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")

    ledger.connection.execute("DROP TABLE intent_claim_projection")
    ledger.connection.commit()

    # get_claim() derives straight from the ledger -- it never touches
    # intent_claim_projection at all.
    info = get_claim(ledger, intent_id)
    assert info is not None

    rebuild_claim_projection(ledger)
    assert _claim_projection_row(ledger, intent_id) is not None


# 22. rebuild deterministic
def test_rebuild_deterministic(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")

    rebuild_claim_projection(ledger)
    first = _claim_projection_row(ledger, intent_id)
    rebuild_claim_projection(ledger)
    second = _claim_projection_row(ledger, intent_id)
    assert first == second


# 23. rebuild idempotent
def test_rebuild_idempotent(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")
    claims.release_claim(intent_id, "owner-a")

    rebuild_claim_projection(ledger)
    rows_after_first = ledger.connection.execute("SELECT * FROM intent_claim_projection").fetchall()
    rebuild_claim_projection(ledger)
    rows_after_second = ledger.connection.execute("SELECT * FROM intent_claim_projection").fetchall()
    assert rows_after_first == rows_after_second == []


# 24. audit validates claims
def test_audit_validates_claims(ledger, claims, inbox):
    intent_id_1 = _submit(inbox)
    claims.claim_intent(intent_id_1, "owner-a")
    intent_id_2 = _submit(inbox)
    claims.claim_intent(intent_id_2, "owner-b")
    claims.release_claim(intent_id_2, "owner-b")

    result = audit(ledger)
    assert result.success
    assert result.projections_valid


# 25. audit remains read-only
def test_audit_remains_read_only_for_claims(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")

    before_ledger = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    before_proj = ledger.connection.execute(
        "SELECT * FROM intent_claim_projection ORDER BY intent_id"
    ).fetchall()

    audit(ledger)

    after_ledger = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    after_proj = ledger.connection.execute(
        "SELECT * FROM intent_claim_projection ORDER BY intent_id"
    ).fetchall()

    assert before_ledger == after_ledger
    assert before_proj == after_proj


# 26. dispatcher requires claim before execution
def test_dispatcher_requires_claim_before_execution(ledger, lifecycle, inbox, claims):
    intent_id = _submit(inbox)
    # Claimed under a different owner first -- dispatch_and_track must
    # refuse, since it cannot obtain the claim itself.
    claims.claim_intent(intent_id, "someone-else")

    result = dispatch_and_track(ledger, lifecycle, claims, intent_id, "dispatcher-1", EchoWorker())
    assert not result.success
    assert result.error_type == "ALREADY_CLAIMED"
    # Nothing executed: the intent is still PENDING per the unmodified inbox state machine.
    assert get_intent_status(ledger, intent_id) == "PENDING"


# 27. dispatcher releases claim after terminal state
def test_dispatcher_releases_claim_after_terminal_state(ledger, lifecycle, inbox, claims):
    intent_id = _submit(inbox)
    result = dispatch_and_track(ledger, lifecycle, claims, intent_id, "dispatcher-1", EchoWorker())
    assert result.success
    assert result.status == "COMPLETED"
    assert result.release_error_type is None
    assert get_claim(ledger, intent_id) is None


# 28. release failure remains visible
def test_release_failure_remains_visible(ledger, lifecycle, inbox, claims, monkeypatch):
    intent_id = _submit(inbox)

    def failing_release(intent_id_arg, owner_id_arg):
        return ReleaseResult(success=False, error_type="STORAGE_FAILURE")

    monkeypatch.setattr(claims, "release_claim", failing_release)

    result = dispatch_and_track(ledger, lifecycle, claims, intent_id, "dispatcher-1", EchoWorker())
    assert result.success  # dispatch itself succeeded
    assert result.status == "COMPLETED"
    assert result.release_error_type == "STORAGE_FAILURE"

    monkeypatch.undo()
    # No release event was ever truly appended -- the claim is still active.
    assert get_claim(ledger, intent_id) is not None


# 29 / 30 / 31 / 32. no leases, no expiration, no stealing, no orphan cleanup
def test_no_leases_expiration_stealing_or_orphan_cleanup():
    # Word-boundary matching, not plain substring: "release"/"releases"
    # legitimately contain the letters "lease" without meaning one.
    forbidden = (r"\blease", "expir", "steal", "cleanup", "ttl", "heartbeat")
    sources = "".join(
        inspect.getsource(fn)
        for fn in (
            ClaimKernel.claim_intent,
            ClaimKernel.release_claim,
            get_claim,
            rebuild_claim_projection,
        )
    ).lower()
    for word in forbidden:
        assert re.search(word, sources) is None


# 33. 100-intent claim/release stress test
def test_hundred_intent_claim_release_stress_test(ledger, claims, inbox):
    intent_ids = [_submit(inbox, {"n": i}) for i in range(100)]

    for intent_id in intent_ids:
        result = claims.claim_intent(intent_id, f"owner-{intent_id}")
        assert result.success

    for intent_id in intent_ids:
        duplicate = claims.claim_intent(intent_id, "intruder")
        assert not duplicate.success
        assert duplicate.error_type == "ALREADY_CLAIMED"

    for intent_id in intent_ids:
        result = claims.release_claim(intent_id, f"owner-{intent_id}")
        assert result.success
        assert get_claim(ledger, intent_id) is None

    rebuild_claim_projection(ledger)
    rows = ledger.connection.execute("SELECT * FROM intent_claim_projection").fetchall()
    assert rows == []

    assert audit(ledger).success

    # One orphaned claim left active on purpose: audit must report it
    # as active, visible state -- never as automatically wrong.
    orphan_intent_id = _submit(inbox, {"orphan": True})
    claims.claim_intent(orphan_intent_id, "owner-orphan")

    rebuild_claim_projection(ledger)
    orphan_row = ledger.connection.execute(
        "SELECT owner_id FROM intent_claim_projection WHERE intent_id = ?",
        (orphan_intent_id,),
    ).fetchone()
    assert orphan_row is not None
    assert orphan_row[0] == "owner-orphan"

    assert audit(ledger).success


# 34. validate_chain() still succeeds
def test_validate_chain_still_succeeds(ledger, claims, inbox):
    intent_id = _submit(inbox)
    claims.claim_intent(intent_id, "owner-a")
    claims.release_claim(intent_id, "owner-a")

    intent_id_2 = _submit(inbox)
    claims.claim_intent(intent_id_2, "owner-b")  # left orphaned, deliberately

    result = ledger.validate_chain()
    assert result.success


# 35. typed failures preferred over exceptions
def test_typed_failures_preferred_over_exceptions(claims, inbox):
    intent_id = _submit(inbox)

    for args in [("does-not-exist", "owner-a"), (intent_id, ""), (intent_id, 123)]:
        result = claims.claim_intent(*args)
        assert isinstance(result, ClaimResult)
        assert not result.success
        assert result.error_type is not None

    for args in [(intent_id, "owner-a"), (intent_id, "")]:
        result = claims.release_claim(*args)
        assert isinstance(result, ReleaseResult)
        assert not result.success
        assert result.error_type is not None
