from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.implementation_report.report import (
    PROVENANCE_NOTICE,
    ImplementationReport,
    create_implementation_report,
    implementation_report_markdown,
    write_implementation_report,
)
from leira.inbox.inbox import InboxKernel
from leira.project_state.state import build_project_state


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _report():
    return create_implementation_report(
        report_id="report-1",
        dispatch_id="dispatch-1",
        implementer_label="Claude",
        source_label="implementer_submission",
        files_created=("leira/foo/__init__.py", "leira/foo/foo.py"),
        files_modified=("leira/bar/bar.py", "leira/baz/baz.py"),
        files_deleted=("leira/old/old.py", "leira/old/stale.py"),
        commands_run=("pytest leira/foo", "pytest -q"),
        reported_results=("all tests passed", "lint clean"),
        reported_blockers=("missing fixture data", "flaky network test"),
        reason_codes=("scope_complete", "tests_added"),
        report_body="Implemented the foo slice exactly as specified.\nNo deviations.",
    )


def test_immutable_dataclass():
    report = _report()
    with pytest.raises(FrozenInstanceError):
        report.implementer_label = "Aura"


def test_deterministic_object_creation():
    assert _report() == _report()
    assert isinstance(_report(), ImplementationReport)


def test_deterministic_markdown():
    first = implementation_report_markdown(_report())
    second = implementation_report_markdown(_report())
    assert first == second
    assert first.startswith("# Implementation Report Record\n")


def test_byte_identical_repeated_rendering():
    first = implementation_report_markdown(_report()).encode("utf-8")
    second = implementation_report_markdown(_report()).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    report = _report()
    path = write_implementation_report(report, tmp_path)
    assert path == ".leira/implementation_reports/report-1.implementation.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == implementation_report_markdown(report)


def test_byte_identical_repeated_writes(tmp_path):
    report = _report()
    first_path = write_implementation_report(report, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_implementation_report(report, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_implementer_label_preserved_exactly():
    label = " Claude / Seat A "
    report = create_implementation_report(
        report_id="report-1",
        dispatch_id="dispatch-1",
        implementer_label=label,
        source_label="source",
        files_created=(),
        files_modified=(),
        files_deleted=(),
        commands_run=(),
        reported_results=(),
        reported_blockers=(),
        reason_codes=(),
        report_body="body",
    )
    assert report.implementer_label == label
    assert label in implementation_report_markdown(report)


def test_source_label_preserved_exactly():
    label = "external/ci-runner"
    report = create_implementation_report(
        report_id="report-1",
        dispatch_id="dispatch-1",
        implementer_label="Claude",
        source_label=label,
        files_created=(),
        files_modified=(),
        files_deleted=(),
        commands_run=(),
        reported_results=(),
        reported_blockers=(),
        reason_codes=(),
        report_body="body",
    )
    assert report.source_label == label
    assert label in implementation_report_markdown(report)


def test_files_created_ordering_preserved_exactly():
    report = _report()
    assert report.files_created == ("leira/foo/__init__.py", "leira/foo/foo.py")
    markdown = implementation_report_markdown(report)
    assert markdown.index("* leira/foo/__init__.py") < markdown.index("* leira/foo/foo.py")


def test_files_modified_ordering_preserved_exactly():
    report = _report()
    assert report.files_modified == ("leira/bar/bar.py", "leira/baz/baz.py")
    markdown = implementation_report_markdown(report)
    assert markdown.index("* leira/bar/bar.py") < markdown.index("* leira/baz/baz.py")


def test_files_deleted_ordering_preserved_exactly():
    report = _report()
    assert report.files_deleted == ("leira/old/old.py", "leira/old/stale.py")
    markdown = implementation_report_markdown(report)
    assert markdown.index("* leira/old/old.py") < markdown.index("* leira/old/stale.py")


def test_commands_run_ordering_preserved_exactly():
    report = _report()
    assert report.commands_run == ("pytest leira/foo", "pytest -q")
    markdown = implementation_report_markdown(report)
    assert markdown.index("* pytest leira/foo") < markdown.index("* pytest -q")


def test_reported_results_ordering_preserved_exactly():
    report = _report()
    assert report.reported_results == ("all tests passed", "lint clean")
    markdown = implementation_report_markdown(report)
    assert markdown.index("* all tests passed") < markdown.index("* lint clean")


def test_reported_blockers_ordering_preserved_exactly():
    report = _report()
    assert report.reported_blockers == ("missing fixture data", "flaky network test")
    markdown = implementation_report_markdown(report)
    assert markdown.index("* missing fixture data") < markdown.index("* flaky network test")


def test_reason_codes_ordering_preserved_exactly():
    report = _report()
    assert report.reason_codes == ("scope_complete", "tests_added")
    markdown = implementation_report_markdown(report)
    assert markdown.index("* scope_complete") < markdown.index("* tests_added")


def test_report_body_preserved_exactly():
    body = "Line 1\n\nLine 3 with trailing spaces  "
    report = create_implementation_report(
        report_id="report-1",
        dispatch_id="dispatch-1",
        implementer_label="Claude",
        source_label="source",
        files_created=(),
        files_modified=(),
        files_deleted=(),
        commands_run=(),
        reported_results=(),
        reported_blockers=(),
        reason_codes=(),
        report_body=body,
    )
    assert report.report_body == body
    assert body in implementation_report_markdown(report)


def test_section_order_never_varies():
    markdown = implementation_report_markdown(_report())
    sections = [
        "# Implementation Report Record",
        "## Report ID",
        "## Dispatch",
        "## Implementer",
        "## Source",
        "## Files Created",
        "## Files Modified",
        "## Files Deleted",
        "## Commands Run",
        "## Reported Results",
        "## Reported Blockers",
        "## Reason Codes",
        "## Report Body",
        "## Provenance Notice",
    ]
    positions = [markdown.index(section) for section in sections]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/implementation_report/report.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_generated_identifiers():
    first = create_implementation_report(
        report_id="report-1",
        dispatch_id="dispatch-1",
        implementer_label="Claude",
        source_label="source",
        files_created=(),
        files_modified=(),
        files_deleted=(),
        commands_run=(),
        reported_results=(),
        reported_blockers=(),
        reason_codes=(),
        report_body="body",
    )
    second = create_implementation_report(
        report_id="report-1",
        dispatch_id="dispatch-1",
        implementer_label="Claude",
        source_label="source",
        files_created=(),
        files_modified=(),
        files_deleted=(),
        commands_run=(),
        reported_results=(),
        reported_blockers=(),
        reason_codes=(),
        report_body="body",
    )
    assert first.report_id == second.report_id == "report-1"


def test_no_verification_or_approval_logic():
    report = _report()
    assert not hasattr(report, "verified")
    assert not hasattr(report, "approved")
    assert not hasattr(report, "success")
    assert not hasattr(report, "verify")
    assert not hasattr(report, "approve")


def test_no_execution_or_subprocess():
    source = (_repo_root() / "leira/implementation_report/report.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_no_ledger_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        implementation_report_markdown(_report())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        implementation_report_markdown(_report())
        write_implementation_report(_report(), tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_dispatcher_imports():
    source = (_repo_root() / "leira/implementation_report/report.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source


def test_no_planner_or_ai_calls():
    source = (_repo_root() / "leira/implementation_report/report.py").read_text(encoding="utf-8")
    forbidden = ("planner", "openai", "anthropic", "claude_api", "browser")
    assert all(term not in source.lower() for term in forbidden)


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/implementation_report/verification.py",
        root / "leira/implementation_report/approval.py",
        root / "leira/implementation_report/rejection.py",
        root / "leira/implementation_report/dispatcher.py",
        root / "leira/implementation_report/planner.py",
        root / "leira/implementation_report/cli.py",
        root / "leira/implementation_report/openai.py",
        root / "leira/implementation_report/claude.py",
        root / "leira/implementation_report/browser.py",
        root / "leira/implementation_report/database.py",
        root / "leira/implementation_report/execution.py",
        root / "leira/implementation_report/subprocess.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    markdown = implementation_report_markdown(_report())
    assert PROVENANCE_NOTICE in markdown
