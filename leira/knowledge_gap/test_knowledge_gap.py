from pathlib import Path

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.knowledge_gap.gap import (
    DEFAULT_LEIRA_REVIEW_QUESTION,
    NON_CONSENSUS_NOTICE,
    create_knowledge_gap,
    create_review_question,
    review_question_markdown,
    write_review_question,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _gap():
    return create_knowledge_gap(
        draft_id="draft-1",
        question="What invariant is unclear?",
        category="invariant",
        source_label="review_policy",
        reason_codes=("invariant_unclear", "missing_evidence"),
    )


def test_deterministic_knowledge_gap_creation():
    first = _gap()
    second = _gap()
    assert first == second


def test_deterministic_review_question_creation():
    gap = _gap()
    first = create_review_question(gap, target_reviewers=("Aura", "Claude"))
    second = create_review_question(gap, target_reviewers=("Aura", "Claude"))
    assert first == second


def test_default_leira_review_question_text_is_stable():
    assert DEFAULT_LEIRA_REVIEW_QUESTION == (
        "What is the single highest-leverage question, distinction, or hidden assumption "
        "that, if surfaced now, would most improve the likelihood that this work reaches "
        "its intended destination while remaining faithful to its mission?"
    )


def test_same_input_produces_byte_identical_markdown(tmp_path):
    question = create_review_question(_gap(), target_reviewers=("Aura", "Aether"))
    first = review_question_markdown(question)
    second = review_question_markdown(question)
    assert first.encode("utf-8") == second.encode("utf-8")

    first_path = write_review_question(question, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_review_question(question, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_target_reviewers_are_preserved_exactly():
    reviewers = ("Claude", "Aura", "Aether")
    question = create_review_question(_gap(), target_reviewers=reviewers)
    assert question.target_reviewers == reviewers


def test_reason_codes_are_preserved_deterministically():
    gap = _gap()
    assert gap.reason_codes == ("invariant_unclear", "missing_evidence")
    markdown = review_question_markdown(create_review_question(gap, target_reviewers=("Aura",)))
    assert "* invariant_unclear" in markdown
    assert "* missing_evidence" in markdown


def test_no_ledger_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        question = create_review_question(_gap(), target_reviewers=("Aura",))
        review_question_markdown(question)
        assert _event_rows(ledger) == before
    finally:
        ledger.close()


def test_no_feedback_revision_approval_dispatch_cli_mind_or_adapter_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/knowledge_gap/feedback.py",
        root / "leira/knowledge_gap/revision.py",
        root / "leira/knowledge_gap/approval.py",
        root / "leira/knowledge_gap/dispatch.py",
        root / "leira/knowledge_gap/cli.py",
        root / "leira/knowledge_gap/mind.py",
        root / "leira/knowledge_gap/openai.py",
        root / "leira/knowledge_gap/claude.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_non_consensus_notice_is_present():
    markdown = review_question_markdown(create_review_question(_gap(), target_reviewers=("Aura",)))
    assert NON_CONSENSUS_NOTICE in markdown
