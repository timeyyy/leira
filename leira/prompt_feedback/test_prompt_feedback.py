from pathlib import Path

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.prompt_feedback.feedback import (
    NON_RECONCILIATION_NOTICE,
    bundle_feedback,
    feedback_markdown,
    record_feedback,
    write_feedback_bundle,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def test_deterministic_feedback_hash():
    first = record_feedback(
        draft_id="draft-1",
        participant="Tim",
        feedback_text="Keep this narrow.",
        source_label="human",
    )
    second = record_feedback(
        draft_id="draft-1",
        participant="Tim",
        feedback_text="Keep this narrow.",
        source_label="human",
    )
    assert first.feedback_hash == second.feedback_hash
    assert len(first.feedback_hash) == 64


def test_same_input_produces_same_feedback_record():
    first = record_feedback(
        draft_id="draft-1",
        participant="Aura",
        feedback_text="This needs review.",
        source_label="seat",
    )
    second = record_feedback(
        draft_id="draft-1",
        participant="Aura",
        feedback_text="This needs review.",
        source_label="seat",
    )
    assert first == second


def test_feedback_bundle_ordering_is_deterministic():
    zed = record_feedback(
        draft_id="draft-1",
        participant="Zed",
        feedback_text="last",
        source_label="seat",
    )
    aura = record_feedback(
        draft_id="draft-1",
        participant="Aura",
        feedback_text="first",
        source_label="seat",
    )
    bundle = bundle_feedback("draft-1", [zed, aura])
    assert [record.participant for record in bundle.feedback_records] == ["Aura", "Zed"]


def test_optional_markdown_output_is_byte_identical_across_repeated_runs(tmp_path):
    records = [
        record_feedback(
            draft_id="draft-1",
            participant="Tim",
            feedback_text="Preserve this.",
            source_label="human",
        )
    ]
    bundle = bundle_feedback("draft-1", records)
    first_path = write_feedback_bundle(bundle, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_feedback_bundle(bundle, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_no_ledger_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        record = record_feedback(
            draft_id="draft-1",
            participant="Claude",
            feedback_text="No ledger writes.",
            source_label="seat",
        )
        bundle = bundle_feedback("draft-1", [record])
        feedback_markdown(bundle)
        assert _event_rows(ledger) == before
    finally:
        ledger.close()


def test_feedback_is_not_classified():
    record = record_feedback(
        draft_id="draft-1",
        participant="Aether",
        feedback_text="This may be high risk.",
        source_label="seat",
    )
    assert not hasattr(record, "classification")
    assert not hasattr(record, "risk_level")
    assert not hasattr(record, "decision")


def test_disagreement_is_preserved_as_text_not_resolved():
    text = "Aura says preserve it. Aether says compost it."
    record = record_feedback(
        draft_id="draft-1",
        participant="Codex",
        feedback_text=text,
        source_label="seat",
    )
    bundle = bundle_feedback("draft-1", [record])
    markdown = feedback_markdown(bundle)
    assert text in markdown
    assert NON_RECONCILIATION_NOTICE in markdown
    assert "approved" not in markdown.lower()
    assert "rejected" not in markdown.lower()


def test_no_proposal_approval_revision_dispatch_cli_mind_or_adapter_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/prompt_feedback/proposal.py",
        root / "leira/prompt_feedback/approval.py",
        root / "leira/prompt_feedback/revision.py",
        root / "leira/prompt_feedback/dispatch.py",
        root / "leira/prompt_feedback/cli.py",
        root / "leira/prompt_feedback/mind.py",
        root / "leira/prompt_feedback/openai.py",
        root / "leira/prompt_feedback/claude.py",
    ]
    assert all(not path.exists() for path in forbidden)
