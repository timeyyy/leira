from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.human_decision.decision import (
    PROVENANCE_NOTICE,
    HumanDecision,
    create_human_decision,
    human_decision_markdown,
    write_human_decision,
)
from leira.inbox.inbox import InboxKernel


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _decision():
    return create_human_decision(
        decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        decision_type="approve",
        reason_codes=("evidence_sufficient", "risk_acceptable"),
        source_label="human_operator",
        review_record_ids=("review-b", "review-a"),
        prompt_revision_id="revision-1",
        decision_summary="Approved after reviewing the revised prompt.",
    )


def test_immutable_dataclass():
    decision = _decision()
    with pytest.raises(FrozenInstanceError):
        decision.decision_type = "reject"


def test_deterministic_object_creation():
    assert _decision() == _decision()
    assert isinstance(_decision(), HumanDecision)


def test_deterministic_markdown():
    first = human_decision_markdown(_decision())
    second = human_decision_markdown(_decision())
    assert first == second
    assert first.startswith("# Human Decision Record\n")


def test_byte_identical_repeated_rendering():
    first = human_decision_markdown(_decision()).encode("utf-8")
    second = human_decision_markdown(_decision()).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    decision = _decision()
    path = write_human_decision(decision, tmp_path)
    assert path == ".leira/human_decisions/decision-1.decision.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == human_decision_markdown(decision)


def test_byte_identical_repeated_writes(tmp_path):
    decision = _decision()
    first_path = write_human_decision(decision, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_human_decision(decision, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_decision_type_preserved_exactly():
    decision = create_human_decision(
        decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        decision_type="something_unrecognized",
        reason_codes=(),
        source_label="source",
        review_record_ids=(),
        prompt_revision_id="revision-1",
        decision_summary="summary",
    )
    assert decision.decision_type == "something_unrecognized"
    assert "something_unrecognized" in human_decision_markdown(decision)


def test_review_record_ordering_preserved_exactly():
    decision = _decision()
    assert decision.review_record_ids == ("review-b", "review-a")
    markdown = human_decision_markdown(decision)
    assert markdown.index("* review-b") < markdown.index("* review-a")


def test_reason_code_ordering_preserved_exactly():
    decision = _decision()
    assert decision.reason_codes == ("evidence_sufficient", "risk_acceptable")
    markdown = human_decision_markdown(decision)
    assert markdown.index("* evidence_sufficient") < markdown.index("* risk_acceptable")


def test_decision_summary_preserved_exactly():
    summary = "Summary line 1\n\nSummary line 3 with trailing spaces  "
    decision = create_human_decision(
        decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        decision_type="defer",
        reason_codes=(),
        source_label="source",
        review_record_ids=(),
        prompt_revision_id="revision-1",
        decision_summary=summary,
    )
    assert decision.decision_summary == summary
    assert summary in human_decision_markdown(decision)


def test_section_order_never_varies():
    markdown = human_decision_markdown(_decision())
    sections = [
        "# Human Decision Record",
        "## Decision ID",
        "## Subject",
        "## Subject Kind",
        "## Decision Type",
        "## Prompt Revision",
        "## Review Records",
        "## Source",
        "## Reason Codes",
        "## Decision Summary",
        "## Provenance Notice",
    ]
    positions = [markdown.index(section) for section in sections]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/human_decision/decision.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_generated_identifiers():
    first = create_human_decision(
        decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        decision_type="approve",
        reason_codes=(),
        source_label="source",
        review_record_ids=(),
        prompt_revision_id="revision-1",
        decision_summary="summary",
    )
    second = create_human_decision(
        decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        decision_type="approve",
        reason_codes=(),
        source_label="source",
        review_record_ids=(),
        prompt_revision_id="revision-1",
        decision_summary="summary",
    )
    assert first.decision_id == second.decision_id == "decision-1"


def test_no_ledger_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        human_decision_markdown(_decision())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()


def test_no_dispatcher_imports():
    source = (_repo_root() / "leira/human_decision/decision.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source


def test_no_workflow_execution_planner_ai_or_approval_logic():
    decision = _decision()
    assert not hasattr(decision, "execute")
    assert not hasattr(decision, "dispatch")
    assert not hasattr(decision, "plan")
    assert not hasattr(decision, "approval")
    assert not hasattr(decision, "next_state")


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/human_decision/dispatcher.py",
        root / "leira/human_decision/workflow.py",
        root / "leira/human_decision/approval.py",
        root / "leira/human_decision/planner.py",
        root / "leira/human_decision/state_machine.py",
        root / "leira/human_decision/cli.py",
        root / "leira/human_decision/openai.py",
        root / "leira/human_decision/claude.py",
        root / "leira/human_decision/browser.py",
        root / "leira/human_decision/database.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    markdown = human_decision_markdown(_decision())
    assert PROVENANCE_NOTICE in markdown
