from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.prompt_revision.revision import (
    PROVENANCE_NOTICE,
    PromptRevision,
    create_prompt_revision,
    prompt_revision_markdown,
    write_prompt_revision,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _revision():
    return create_prompt_revision(
        revision_id="revision-1",
        parent_draft_id="draft-1",
        review_record_ids=("review-b", "review-a"),
        reason_codes=("scope_narrowed", "invariant_added"),
        source_label="human_revision",
        revised_prompt="Implement the revised slice.\nPreserve this text exactly.",
        revision_summary="Narrowed scope after review.",
    )


def test_immutable_dataclass():
    revision = _revision()
    with pytest.raises(FrozenInstanceError):
        revision.revision_id = "revision-2"


def test_deterministic_object_creation():
    assert _revision() == _revision()
    assert isinstance(_revision(), PromptRevision)


def test_deterministic_markdown():
    first = prompt_revision_markdown(_revision())
    second = prompt_revision_markdown(_revision())
    assert first == second
    assert first.startswith("# Prompt Revision Record\n")


def test_deterministic_file_output(tmp_path):
    revision = _revision()
    path = write_prompt_revision(revision, tmp_path)
    assert path == ".leira/prompt_revisions/revision-1.revision.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == prompt_revision_markdown(revision)


def test_repeated_writes_produce_identical_bytes(tmp_path):
    revision = _revision()
    first_path = write_prompt_revision(revision, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_prompt_revision(revision, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_review_record_ordering_preserved_exactly():
    revision = _revision()
    assert revision.review_record_ids == ("review-b", "review-a")
    markdown = prompt_revision_markdown(revision)
    assert markdown.index("* review-b") < markdown.index("* review-a")


def test_reason_code_ordering_preserved_exactly():
    revision = _revision()
    assert revision.reason_codes == ("scope_narrowed", "invariant_added")
    markdown = prompt_revision_markdown(revision)
    assert markdown.index("* scope_narrowed") < markdown.index("* invariant_added")


def test_revised_prompt_preserved_exactly():
    prompt = "Line 1\n\nLine 3 with trailing spaces  "
    revision = create_prompt_revision(
        revision_id="revision-1",
        parent_draft_id="draft-1",
        review_record_ids=(),
        reason_codes=(),
        source_label="human_revision",
        revised_prompt=prompt,
        revision_summary="summary",
    )
    assert revision.revised_prompt == prompt
    assert f"```text\n{prompt}\n```" in prompt_revision_markdown(revision)


def test_revision_summary_preserved_exactly():
    summary = "Summary line 1\nSummary line 2  "
    revision = create_prompt_revision(
        revision_id="revision-1",
        parent_draft_id="draft-1",
        review_record_ids=(),
        reason_codes=(),
        source_label="human_revision",
        revised_prompt="prompt",
        revision_summary=summary,
    )
    assert revision.revision_summary == summary
    assert summary in prompt_revision_markdown(revision)


def test_no_clocks_timestamps_uuid_randomness_or_generated_identifiers():
    source = (_repo_root() / "leira/prompt_revision/revision.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_ledger_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        prompt_revision_markdown(_revision())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()


def test_no_dispatcher_imports():
    source = (_repo_root() / "leira/prompt_revision/revision.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source


def test_no_approval_rejection_planning_ai_semantic_analysis_or_prompt_generation():
    revision = _revision()
    assert not hasattr(revision, "approval")
    assert not hasattr(revision, "rejection")
    assert not hasattr(revision, "plan")
    assert not hasattr(revision, "analysis")
    assert not hasattr(revision, "generated_prompt")


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/prompt_revision/approval.py",
        root / "leira/prompt_revision/rejection.py",
        root / "leira/prompt_revision/planner.py",
        root / "leira/prompt_revision/dispatcher.py",
        root / "leira/prompt_revision/cli.py",
        root / "leira/prompt_revision/openai.py",
        root / "leira/prompt_revision/claude.py",
        root / "leira/prompt_revision/browser.py",
        root / "leira/prompt_revision/database.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    assert PROVENANCE_NOTICE in prompt_revision_markdown(_revision())
