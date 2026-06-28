from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.lifecycle_projection.lifecycle import EVIDENCE_LABELS, build_lifecycle_projection
from leira.missing_evidence_projection.missing_evidence import (
    PROVENANCE_NOTICE,
    MissingEvidence,
    MissingEvidenceProjection,
    build_missing_evidence_projection,
    missing_evidence_projection_markdown,
    write_missing_evidence_projection,
)
from leira.project_state.state import build_project_state


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _all_absent():
    return build_lifecycle_projection(subject_id="subject-1", subject_kind="prompt_draft")


def _all_present():
    return build_lifecycle_projection(
        subject_id="subject-1",
        subject_kind="prompt_draft",
        prompt_draft="x",
        knowledge_gap="x",
        review_question="x",
        review_record="x",
        prompt_revision="x",
        human_decision="x",
        dispatch_record="x",
        implementation_report="x",
        verification_record="x",
    )


def _mixed():
    return build_lifecycle_projection(
        subject_id="subject-1",
        subject_kind="prompt_draft",
        prompt_draft="x",
        knowledge_gap="x",
        review_question="x",
    )


def test_missing_evidence_immutable():
    entry = MissingEvidence(label="Review Record")
    with pytest.raises(FrozenInstanceError):
        entry.label = "Other"


def test_missing_evidence_projection_immutable():
    projection = build_missing_evidence_projection(_mixed())
    with pytest.raises(FrozenInstanceError):
        projection.subject_id = "other"


def test_deterministic_projection_creation():
    lp = _mixed()
    first = build_missing_evidence_projection(lp)
    second = build_missing_evidence_projection(lp)
    assert first == second
    assert isinstance(first, MissingEvidenceProjection)


def test_deterministic_markdown():
    projection = build_missing_evidence_projection(_mixed())
    first = missing_evidence_projection_markdown(projection)
    second = missing_evidence_projection_markdown(projection)
    assert first == second
    assert first.startswith("# Missing Evidence Projection\n")


def test_byte_identical_repeated_rendering():
    projection = build_missing_evidence_projection(_mixed())
    first = missing_evidence_projection_markdown(projection).encode("utf-8")
    second = missing_evidence_projection_markdown(projection).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    projection = build_missing_evidence_projection(_mixed())
    path = write_missing_evidence_projection(projection, tmp_path)
    assert path == ".leira/missing_evidence/subject-1.missing.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == missing_evidence_projection_markdown(
        projection
    )


def test_byte_identical_repeated_writes(tmp_path):
    projection = build_missing_evidence_projection(_mixed())
    first_path = write_missing_evidence_projection(projection, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_missing_evidence_projection(projection, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_lifecycle_ordering_preserved_exactly_for_missing():
    projection = build_missing_evidence_projection(_mixed())
    assert projection.missing_evidence == (
        MissingEvidence("Review Record"),
        MissingEvidence("Prompt Revision"),
        MissingEvidence("Human Decision"),
        MissingEvidence("Dispatch Record"),
        MissingEvidence("Implementation Report"),
        MissingEvidence("Verification Record"),
    )
    markdown = missing_evidence_projection_markdown(projection)
    positions = [markdown.index(f"* {entry.label}") for entry in projection.missing_evidence]
    assert positions == sorted(positions)


def test_lifecycle_ordering_preserved_exactly_for_completed():
    projection = build_missing_evidence_projection(_mixed())
    assert projection.completed_evidence == ("Prompt Draft", "Knowledge Gap", "Review Question")
    markdown = missing_evidence_projection_markdown(projection)
    positions = [markdown.index(f"* {label}") for label in projection.completed_evidence]
    assert positions == sorted(positions)


def test_missing_count_correct():
    projection = build_missing_evidence_projection(_mixed())
    assert projection.missing_count == 6
    assert projection.missing_count == len(projection.missing_evidence)


def test_completed_count_correct():
    projection = build_missing_evidence_projection(_mixed())
    assert projection.completed_count == 3
    assert projection.completed_count == len(projection.completed_evidence)


def test_all_evidence_present():
    projection = build_missing_evidence_projection(_all_present())
    assert projection.missing_evidence == ()
    assert projection.missing_count == 0
    assert projection.completed_count == len(EVIDENCE_LABELS)
    assert projection.completed_evidence == EVIDENCE_LABELS


def test_all_evidence_missing():
    projection = build_missing_evidence_projection(_all_absent())
    assert projection.completed_evidence == ()
    assert projection.completed_count == 0
    assert projection.missing_count == len(EVIDENCE_LABELS)
    assert tuple(entry.label for entry in projection.missing_evidence) == EVIDENCE_LABELS


def test_mixed_evidence():
    projection = build_missing_evidence_projection(_mixed())
    assert projection.missing_count == 6
    assert projection.completed_count == 3
    assert projection.missing_count + projection.completed_count == len(EVIDENCE_LABELS)


def test_section_order_never_varies():
    markdown = missing_evidence_projection_markdown(build_missing_evidence_projection(_mixed()))
    sections = [
        "# Missing Evidence Projection",
        "## Subject",
        "## Missing Evidence",
        "## Completed Evidence",
        "## Counts",
        "## Provenance Notice",
    ]
    positions = [markdown.index(section) for section in sections]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/missing_evidence_projection/missing_evidence.py").read_text(
        encoding="utf-8"
    )
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_repository_scanning_or_filesystem_inspection():
    source = (_repo_root() / "leira/missing_evidence_projection/missing_evidence.py").read_text(
        encoding="utf-8"
    )
    forbidden = ("glob", "os.walk", "os.listdir", "scandir", "iterdir", "Path.cwd")
    assert all(term not in source for term in forbidden)


def test_no_ledger_access(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        missing_evidence_projection_markdown(build_missing_evidence_projection(_mixed()))
        assert _event_rows(ledger) == before
    finally:
        ledger.close()
    source = (_repo_root() / "leira/missing_evidence_projection/missing_evidence.py").read_text(
        encoding="utf-8"
    )
    assert "LedgerKernel" not in source
    assert "ledger.connection" not in source
    assert "ledger_events" not in source


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        projection = build_missing_evidence_projection(_mixed())
        missing_evidence_projection_markdown(projection)
        write_missing_evidence_projection(projection, tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_planner_dispatcher_recommendation_engine_or_flow_policy():
    source = (_repo_root() / "leira/missing_evidence_projection/missing_evidence.py").read_text(
        encoding="utf-8"
    )
    assert "dispatcher" not in source
    forbidden = ("planner", "recommendation_engine", "flow_policy", "flowpolicy", "evaluate_flow_policy")
    assert all(term not in source.lower() for term in forbidden)


def test_no_ai_calls_or_browser_automation():
    source = (_repo_root() / "leira/missing_evidence_projection/missing_evidence.py").read_text(
        encoding="utf-8"
    )
    forbidden = ("openai", "anthropic", "browser", "requests")
    assert all(term not in source.lower() for term in forbidden)


def test_no_subprocess_or_shell_commands():
    source = (_repo_root() / "leira/missing_evidence_projection/missing_evidence.py").read_text(
        encoding="utf-8"
    )
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/missing_evidence_projection/dispatcher.py",
        root / "leira/missing_evidence_projection/planner.py",
        root / "leira/missing_evidence_projection/workflow.py",
        root / "leira/missing_evidence_projection/flow_policy.py",
        root / "leira/missing_evidence_projection/approval.py",
        root / "leira/missing_evidence_projection/rejection.py",
        root / "leira/missing_evidence_projection/cli.py",
        root / "leira/missing_evidence_projection/openai.py",
        root / "leira/missing_evidence_projection/claude.py",
        root / "leira/missing_evidence_projection/browser.py",
        root / "leira/missing_evidence_projection/database.py",
        root / "leira/missing_evidence_projection/scanner.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_does_not_inspect_evidence_or_reconstruct_lifecycle():
    source = (_repo_root() / "leira/missing_evidence_projection/missing_evidence.py").read_text(
        encoding="utf-8"
    )
    assert "build_lifecycle_projection" not in source
    assert "EvidencePresence" not in source


def test_provenance_notice_present():
    projection = build_missing_evidence_projection(_mixed())
    markdown = missing_evidence_projection_markdown(projection)
    assert PROVENANCE_NOTICE in markdown
