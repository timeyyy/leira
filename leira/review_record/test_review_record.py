from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.review_record.review_record import (
    PROVENANCE_NOTICE,
    ReviewRecord,
    create_review_record,
    review_record_markdown,
    write_review_record,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _record():
    return create_review_record(
        review_id="review-1",
        draft_id="draft-1",
        review_question_id="question-1",
        reviewer_label="Claude",
        response_text="Preserve this exact response.\nIncluding line breaks.",
        source_label="external_reviewer",
        reason_codes=("missing_evidence", "scope_unclear"),
        attachments=("artifact-a.txt", "artifact-b.txt"),
    )


def test_immutable_dataclass():
    record = _record()
    with pytest.raises(FrozenInstanceError):
        record.reviewer_label = "Aura"


def test_deterministic_object_creation():
    assert _record() == _record()
    assert isinstance(_record(), ReviewRecord)


def test_deterministic_markdown():
    first = review_record_markdown(_record())
    second = review_record_markdown(_record())
    assert first == second
    assert first.startswith("# Review Record\n")


def test_byte_identical_repeated_rendering():
    first = review_record_markdown(_record()).encode("utf-8")
    second = review_record_markdown(_record()).encode("utf-8")
    assert first == second


def test_byte_identical_repeated_file_output(tmp_path):
    record = _record()
    first_path = write_review_record(record, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_review_record(record, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_reviewer_preserved_exactly():
    record = create_review_record(
        review_id="review-1",
        draft_id="draft-1",
        review_question_id="question-1",
        reviewer_label=" Claude / Seat A ",
        response_text="x",
        source_label="source",
        reason_codes=(),
        attachments=(),
    )
    assert record.reviewer_label == " Claude / Seat A "
    assert " Claude / Seat A " in review_record_markdown(record)


def test_response_preserved_exactly():
    text = "Line 1\n\nLine 3 with spaces  "
    record = create_review_record(
        review_id="review-1",
        draft_id="draft-1",
        review_question_id="question-1",
        reviewer_label="Aura",
        response_text=text,
        source_label="source",
        reason_codes=(),
        attachments=(),
    )
    assert record.response_text == text
    assert f"```text\n{text}\n```" in review_record_markdown(record)


def test_reason_code_ordering_preserved():
    record = create_review_record(
        review_id="review-1",
        draft_id="draft-1",
        review_question_id="question-1",
        reviewer_label="Aura",
        response_text="x",
        source_label="source",
        reason_codes=("second", "first"),
        attachments=(),
    )
    assert record.reason_codes == ("second", "first")
    markdown = review_record_markdown(record)
    assert markdown.index("* second") < markdown.index("* first")


def test_attachment_ordering_preserved():
    record = create_review_record(
        review_id="review-1",
        draft_id="draft-1",
        review_question_id="question-1",
        reviewer_label="Aura",
        response_text="x",
        source_label="source",
        reason_codes=(),
        attachments=("b.txt", "a.txt"),
    )
    assert record.attachments == ("b.txt", "a.txt")
    markdown = review_record_markdown(record)
    assert markdown.index("* b.txt") < markdown.index("* a.txt")


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/review_record/review_record.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random")
    assert all(term not in source for term in forbidden)


def test_no_ledger_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        review_record_markdown(_record())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()


def test_no_dispatcher_imports():
    source = (_repo_root() / "leira/review_record/review_record.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source


def test_no_prompt_revision_approval_rejection_or_semantic_analysis():
    record = _record()
    assert not hasattr(record, "revision")
    assert not hasattr(record, "approval")
    assert not hasattr(record, "rejection")
    assert not hasattr(record, "analysis")
    assert not hasattr(record, "classification")


def test_no_reviewer_invocation_or_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/review_record/revision.py",
        root / "leira/review_record/approval.py",
        root / "leira/review_record/rejection.py",
        root / "leira/review_record/dispatch.py",
        root / "leira/review_record/cli.py",
        root / "leira/review_record/mind.py",
        root / "leira/review_record/openai.py",
        root / "leira/review_record/claude.py",
        root / "leira/review_record/database.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    markdown = review_record_markdown(_record())
    assert PROVENANCE_NOTICE in markdown
