from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.project_state.state import build_project_state
from leira.verification_record.verification import (
    PROVENANCE_NOTICE,
    VerificationRecord,
    create_verification_record,
    verification_record_markdown,
    write_verification_record,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _record():
    return create_verification_record(
        verification_id="verification-1",
        implementation_report_id="report-1",
        verifier_label="Claude",
        source_label="independent_verifier",
        checks_run=("pytest leira/foo", "git diff --stat"),
        observed_results=("27 tests passed", "diff matches claim"),
        observed_failures=("one flaky test retried", "lint warning on unused import"),
        observed_files_created=("leira/foo/__init__.py", "leira/foo/foo.py"),
        observed_files_modified=("leira/bar/bar.py", "leira/baz/baz.py"),
        observed_files_deleted=("leira/old/old.py", "leira/old/stale.py"),
        commands_observed=("pytest -q", "git status --short"),
        reason_codes=("claims_match_observations", "tests_independently_run"),
        verification_summary="Independently re-ran the reported commands and confirmed the claimed file changes.",
    )


def test_immutable_dataclass():
    record = _record()
    with pytest.raises(FrozenInstanceError):
        record.verifier_label = "Aura"


def test_deterministic_object_creation():
    assert _record() == _record()
    assert isinstance(_record(), VerificationRecord)


def test_deterministic_markdown():
    first = verification_record_markdown(_record())
    second = verification_record_markdown(_record())
    assert first == second
    assert first.startswith("# Verification Record\n")


def test_byte_identical_repeated_rendering():
    first = verification_record_markdown(_record()).encode("utf-8")
    second = verification_record_markdown(_record()).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    record = _record()
    path = write_verification_record(record, tmp_path)
    assert path == ".leira/verification_records/verification-1.verification.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == verification_record_markdown(record)


def test_byte_identical_repeated_writes(tmp_path):
    record = _record()
    first_path = write_verification_record(record, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_verification_record(record, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_verifier_label_preserved_exactly():
    label = " Claude / Seat A "
    record = create_verification_record(
        verification_id="verification-1",
        implementation_report_id="report-1",
        verifier_label=label,
        source_label="source",
        checks_run=(),
        observed_results=(),
        observed_failures=(),
        observed_files_created=(),
        observed_files_modified=(),
        observed_files_deleted=(),
        commands_observed=(),
        reason_codes=(),
        verification_summary="summary",
    )
    assert record.verifier_label == label
    assert label in verification_record_markdown(record)


def test_source_label_preserved_exactly():
    label = "external/ci-runner"
    record = create_verification_record(
        verification_id="verification-1",
        implementation_report_id="report-1",
        verifier_label="Claude",
        source_label=label,
        checks_run=(),
        observed_results=(),
        observed_failures=(),
        observed_files_created=(),
        observed_files_modified=(),
        observed_files_deleted=(),
        commands_observed=(),
        reason_codes=(),
        verification_summary="summary",
    )
    assert record.source_label == label
    assert label in verification_record_markdown(record)


def test_checks_run_ordering_preserved_exactly():
    record = _record()
    assert record.checks_run == ("pytest leira/foo", "git diff --stat")
    markdown = verification_record_markdown(record)
    assert markdown.index("* pytest leira/foo") < markdown.index("* git diff --stat")


def test_observed_results_ordering_preserved_exactly():
    record = _record()
    assert record.observed_results == ("27 tests passed", "diff matches claim")
    markdown = verification_record_markdown(record)
    assert markdown.index("* 27 tests passed") < markdown.index("* diff matches claim")


def test_observed_failures_ordering_preserved_exactly():
    record = _record()
    assert record.observed_failures == ("one flaky test retried", "lint warning on unused import")
    markdown = verification_record_markdown(record)
    assert markdown.index("* one flaky test retried") < markdown.index("* lint warning on unused import")


def test_observed_files_created_ordering_preserved_exactly():
    record = _record()
    assert record.observed_files_created == ("leira/foo/__init__.py", "leira/foo/foo.py")
    markdown = verification_record_markdown(record)
    assert markdown.index("* leira/foo/__init__.py") < markdown.index("* leira/foo/foo.py")


def test_observed_files_modified_ordering_preserved_exactly():
    record = _record()
    assert record.observed_files_modified == ("leira/bar/bar.py", "leira/baz/baz.py")
    markdown = verification_record_markdown(record)
    assert markdown.index("* leira/bar/bar.py") < markdown.index("* leira/baz/baz.py")


def test_observed_files_deleted_ordering_preserved_exactly():
    record = _record()
    assert record.observed_files_deleted == ("leira/old/old.py", "leira/old/stale.py")
    markdown = verification_record_markdown(record)
    assert markdown.index("* leira/old/old.py") < markdown.index("* leira/old/stale.py")


def test_commands_observed_ordering_preserved_exactly():
    record = _record()
    assert record.commands_observed == ("pytest -q", "git status --short")
    markdown = verification_record_markdown(record)
    assert markdown.index("* pytest -q") < markdown.index("* git status --short")


def test_reason_codes_ordering_preserved_exactly():
    record = _record()
    assert record.reason_codes == ("claims_match_observations", "tests_independently_run")
    markdown = verification_record_markdown(record)
    assert markdown.index("* claims_match_observations") < markdown.index("* tests_independently_run")


def test_verification_summary_preserved_exactly():
    summary = "Line 1\n\nLine 3 with trailing spaces  "
    record = create_verification_record(
        verification_id="verification-1",
        implementation_report_id="report-1",
        verifier_label="Claude",
        source_label="source",
        checks_run=(),
        observed_results=(),
        observed_failures=(),
        observed_files_created=(),
        observed_files_modified=(),
        observed_files_deleted=(),
        commands_observed=(),
        reason_codes=(),
        verification_summary=summary,
    )
    assert record.verification_summary == summary
    assert summary in verification_record_markdown(record)


def test_section_order_never_varies():
    markdown = verification_record_markdown(_record())
    sections = [
        "# Verification Record",
        "## Verification ID",
        "## Implementation Report",
        "## Verifier",
        "## Source",
        "## Checks Run",
        "## Observed Results",
        "## Observed Failures",
        "## Observed Files Created",
        "## Observed Files Modified",
        "## Observed Files Deleted",
        "## Commands Observed",
        "## Reason Codes",
        "## Verification Summary",
        "## Provenance Notice",
    ]
    positions = [markdown.index(section) for section in sections]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/verification_record/verification.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_generated_identifiers():
    first = create_verification_record(
        verification_id="verification-1",
        implementation_report_id="report-1",
        verifier_label="Claude",
        source_label="source",
        checks_run=(),
        observed_results=(),
        observed_failures=(),
        observed_files_created=(),
        observed_files_modified=(),
        observed_files_deleted=(),
        commands_observed=(),
        reason_codes=(),
        verification_summary="summary",
    )
    second = create_verification_record(
        verification_id="verification-1",
        implementation_report_id="report-1",
        verifier_label="Claude",
        source_label="source",
        checks_run=(),
        observed_results=(),
        observed_failures=(),
        observed_files_created=(),
        observed_files_modified=(),
        observed_files_deleted=(),
        commands_observed=(),
        reason_codes=(),
        verification_summary="summary",
    )
    assert first.verification_id == second.verification_id == "verification-1"


def test_no_approval_rejection_or_next_action_decision():
    record = _record()
    assert not hasattr(record, "approved")
    assert not hasattr(record, "rejected")
    assert not hasattr(record, "next_action")
    assert not hasattr(record, "approve")
    assert not hasattr(record, "reject")
    assert not hasattr(record, "decide")


def test_no_dispatch_execution_or_subprocess():
    source = (_repo_root() / "leira/verification_record/verification.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_no_ledger_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        verification_record_markdown(_record())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        verification_record_markdown(_record())
        write_verification_record(_record(), tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_dispatcher_imports():
    source = (_repo_root() / "leira/verification_record/verification.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source


def test_no_planner_or_ai_calls():
    source = (_repo_root() / "leira/verification_record/verification.py").read_text(encoding="utf-8")
    forbidden = ("planner", "openai", "anthropic", "claude_api", "browser")
    assert all(term not in source.lower() for term in forbidden)


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/verification_record/approval.py",
        root / "leira/verification_record/rejection.py",
        root / "leira/verification_record/dispatcher.py",
        root / "leira/verification_record/dispatch.py",
        root / "leira/verification_record/planner.py",
        root / "leira/verification_record/cli.py",
        root / "leira/verification_record/openai.py",
        root / "leira/verification_record/claude.py",
        root / "leira/verification_record/browser.py",
        root / "leira/verification_record/database.py",
        root / "leira/verification_record/execution.py",
        root / "leira/verification_record/subprocess.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    markdown = verification_record_markdown(_record())
    assert PROVENANCE_NOTICE in markdown
