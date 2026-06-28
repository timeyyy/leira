from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.lifecycle_projection.lifecycle import (
    EVIDENCE_LABELS,
    PROVENANCE_NOTICE,
    EvidencePresence,
    LifecycleProjection,
    build_lifecycle_projection,
    evidence_presence_table,
    lifecycle_projection_markdown,
    write_lifecycle_projection,
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
        prompt_draft="evidence",
        knowledge_gap="evidence",
        review_question="evidence",
        review_record="evidence",
        prompt_revision="evidence",
        human_decision="evidence",
        dispatch_record="evidence",
        implementation_report="evidence",
        verification_record="evidence",
    )


def _mixed():
    return build_lifecycle_projection(
        subject_id="subject-1",
        subject_kind="prompt_draft",
        prompt_draft="evidence",
        knowledge_gap="evidence",
        review_question="evidence",
        review_record=None,
        prompt_revision=None,
        human_decision=None,
        dispatch_record=None,
        implementation_report=None,
        verification_record=None,
    )


def test_lifecycle_projection_immutable():
    projection = _all_absent()
    with pytest.raises(FrozenInstanceError):
        projection.subject_id = "subject-2"


def test_evidence_presence_immutable():
    entry = EvidencePresence(label="Prompt Draft", present=True)
    with pytest.raises(FrozenInstanceError):
        entry.present = False


def test_deterministic_projection_creation():
    assert _mixed() == _mixed()
    assert isinstance(_mixed(), LifecycleProjection)


def test_deterministic_markdown():
    first = lifecycle_projection_markdown(_mixed())
    second = lifecycle_projection_markdown(_mixed())
    assert first == second
    assert first.startswith("# Lifecycle Projection\n")


def test_byte_identical_repeated_rendering():
    first = lifecycle_projection_markdown(_mixed()).encode("utf-8")
    second = lifecycle_projection_markdown(_mixed()).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    projection = _mixed()
    path = write_lifecycle_projection(projection, tmp_path)
    assert path == ".leira/lifecycle/subject-1.lifecycle.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == lifecycle_projection_markdown(projection)


def test_byte_identical_repeated_writes(tmp_path):
    projection = _mixed()
    first_path = write_lifecycle_projection(projection, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_lifecycle_projection(projection, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_completed_evidence_ordering_preserved():
    projection = _mixed()
    assert projection.completed_evidence == ("Prompt Draft", "Knowledge Gap", "Review Question")
    markdown = lifecycle_projection_markdown(projection)
    assert markdown.index("* Prompt Draft") < markdown.index("* Knowledge Gap") < markdown.index(
        "* Review Question"
    )


def test_missing_evidence_ordering_preserved():
    projection = _mixed()
    assert projection.missing_evidence == (
        "Review Record",
        "Prompt Revision",
        "Human Decision",
        "Dispatch Record",
        "Implementation Report",
        "Verification Record",
    )
    markdown = lifecycle_projection_markdown(projection)
    positions = [markdown.index(f"* {label}") for label in projection.missing_evidence]
    assert positions == sorted(positions)


def test_presence_flags_correct_for_mixed_evidence():
    projection = _mixed()
    assert projection.prompt_draft_present is True
    assert projection.knowledge_gap_present is True
    assert projection.review_question_present is True
    assert projection.review_record_present is False
    assert projection.prompt_revision_present is False
    assert projection.human_decision_present is False
    assert projection.dispatch_record_present is False
    assert projection.implementation_report_present is False
    assert projection.verification_record_present is False


def test_all_evidence_absent():
    projection = _all_absent()
    assert projection.completed_evidence == ()
    assert projection.missing_evidence == EVIDENCE_LABELS
    assert all(
        not getattr(projection, f"{field}_present")
        for field in (
            "prompt_draft",
            "knowledge_gap",
            "review_question",
            "review_record",
            "prompt_revision",
            "human_decision",
            "dispatch_record",
            "implementation_report",
            "verification_record",
        )
    )
    markdown = lifecycle_projection_markdown(projection)
    assert markdown.count("MISSING") == 9
    assert markdown.count("PRESENT") == 0


def test_all_evidence_present():
    projection = _all_present()
    assert projection.missing_evidence == ()
    assert projection.completed_evidence == EVIDENCE_LABELS
    assert all(
        getattr(projection, f"{field}_present")
        for field in (
            "prompt_draft",
            "knowledge_gap",
            "review_question",
            "review_record",
            "prompt_revision",
            "human_decision",
            "dispatch_record",
            "implementation_report",
            "verification_record",
        )
    )
    markdown = lifecycle_projection_markdown(projection)
    assert markdown.count("PRESENT") == 9
    assert markdown.count("MISSING") == 0


def test_evidence_presence_table_matches_flags():
    projection = _mixed()
    table = evidence_presence_table(projection)
    assert table == (
        EvidencePresence("Prompt Draft", True),
        EvidencePresence("Knowledge Gap", True),
        EvidencePresence("Review Question", True),
        EvidencePresence("Review Record", False),
        EvidencePresence("Prompt Revision", False),
        EvidencePresence("Human Decision", False),
        EvidencePresence("Dispatch Record", False),
        EvidencePresence("Implementation Report", False),
        EvidencePresence("Verification Record", False),
    )


def test_section_order_never_varies():
    markdown = lifecycle_projection_markdown(_mixed())
    sections = [
        "# Lifecycle Projection",
        "## Subject",
        "## Evidence",
        "## Completed Evidence",
        "## Missing Evidence",
        "## Provenance Notice",
    ]
    positions = [markdown.index(section) for section in sections]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/lifecycle_projection/lifecycle.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random")
    assert all(term not in source for term in forbidden)


def test_no_generated_identifiers():
    first = build_lifecycle_projection(subject_id="subject-1", subject_kind="prompt_draft")
    second = build_lifecycle_projection(subject_id="subject-1", subject_kind="prompt_draft")
    assert first.subject_id == second.subject_id == "subject-1"


def test_no_repository_scanning_or_filesystem_inspection():
    source = (_repo_root() / "leira/lifecycle_projection/lifecycle.py").read_text(encoding="utf-8")
    forbidden = ("glob", "os.walk", "os.listdir", "scandir", "iterdir", "Path.cwd")
    assert all(term not in source for term in forbidden)


def test_no_ledger_access(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        lifecycle_projection_markdown(_mixed())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()
    source = (_repo_root() / "leira/lifecycle_projection/lifecycle.py").read_text(encoding="utf-8")
    assert "LedgerKernel" not in source
    assert "ledger.connection" not in source
    assert "ledger_events" not in source
    import_lines = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert all("ledger" not in line.lower() for line in import_lines)


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        lifecycle_projection_markdown(_mixed())
        write_lifecycle_projection(_mixed(), tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_dispatcher_imports():
    source = (_repo_root() / "leira/lifecycle_projection/lifecycle.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source


def test_no_planner_flow_policy_ai_browser_subprocess_or_shell():
    source = (_repo_root() / "leira/lifecycle_projection/lifecycle.py").read_text(encoding="utf-8")
    forbidden = (
        "planner",
        "flow_policy",
        "flowpolicy",
        "openai",
        "anthropic",
        "browser",
        "subprocess",
        "os.system",
        "Popen",
        "exec(",
        "eval(",
    )
    assert all(term not in source.lower() for term in forbidden)


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/lifecycle_projection/dispatcher.py",
        root / "leira/lifecycle_projection/planner.py",
        root / "leira/lifecycle_projection/flow_policy.py",
        root / "leira/lifecycle_projection/workflow.py",
        root / "leira/lifecycle_projection/approval.py",
        root / "leira/lifecycle_projection/rejection.py",
        root / "leira/lifecycle_projection/cli.py",
        root / "leira/lifecycle_projection/openai.py",
        root / "leira/lifecycle_projection/claude.py",
        root / "leira/lifecycle_projection/browser.py",
        root / "leira/lifecycle_projection/database.py",
        root / "leira/lifecycle_projection/scanner.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_evidence_value_unused_only_presence_checked():
    sentinel_a = build_lifecycle_projection(
        subject_id="subject-1", subject_kind="prompt_draft", prompt_draft="value-a"
    )
    sentinel_b = build_lifecycle_projection(
        subject_id="subject-1", subject_kind="prompt_draft", prompt_draft="value-b"
    )
    assert sentinel_a.prompt_draft_present == sentinel_b.prompt_draft_present is True
    assert sentinel_a == sentinel_b


def test_provenance_notice_present():
    markdown = lifecycle_projection_markdown(_mixed())
    assert PROVENANCE_NOTICE in markdown
