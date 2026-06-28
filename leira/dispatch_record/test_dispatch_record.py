from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatch_record.dispatch import (
    PROVENANCE_NOTICE,
    DispatchRecord,
    create_dispatch_record,
    dispatch_record_markdown,
    write_dispatch_record,
)
from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.project_state.state import build_project_state


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _record():
    return create_dispatch_record(
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        dispatch_type="implement",
        target_label="worker_pool_a",
        execution_mode="manual",
        reason_codes=("decision_approved", "evidence_complete"),
        source_label="human_operator",
        dispatch_summary="Intended handoff of the approved draft for implementation.",
    )


def test_immutable_dataclass():
    record = _record()
    with pytest.raises(FrozenInstanceError):
        record.dispatch_type = "cancel"


def test_deterministic_object_creation():
    assert _record() == _record()
    assert isinstance(_record(), DispatchRecord)


def test_deterministic_markdown():
    first = dispatch_record_markdown(_record())
    second = dispatch_record_markdown(_record())
    assert first == second
    assert first.startswith("# Dispatch Record\n")


def test_byte_identical_repeated_rendering():
    first = dispatch_record_markdown(_record()).encode("utf-8")
    second = dispatch_record_markdown(_record()).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    record = _record()
    path = write_dispatch_record(record, tmp_path)
    assert path == ".leira/dispatch_records/dispatch-1.dispatch.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == dispatch_record_markdown(record)


def test_byte_identical_repeated_writes(tmp_path):
    record = _record()
    first_path = write_dispatch_record(record, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_dispatch_record(record, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_dispatch_type_preserved_exactly():
    record = create_dispatch_record(
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        dispatch_type="something_unrecognized",
        target_label="target",
        execution_mode="manual",
        reason_codes=(),
        source_label="source",
        dispatch_summary="summary",
    )
    assert record.dispatch_type == "something_unrecognized"
    assert "something_unrecognized" in dispatch_record_markdown(record)


def test_execution_mode_preserved_exactly():
    record = create_dispatch_record(
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        dispatch_type="implement",
        target_label="target",
        execution_mode="something_unrecognized_mode",
        reason_codes=(),
        source_label="source",
        dispatch_summary="summary",
    )
    assert record.execution_mode == "something_unrecognized_mode"
    assert "something_unrecognized_mode" in dispatch_record_markdown(record)


def test_target_label_preserved_exactly():
    target = " worker-pool / shard-3 "
    record = create_dispatch_record(
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        dispatch_type="implement",
        target_label=target,
        execution_mode="manual",
        reason_codes=(),
        source_label="source",
        dispatch_summary="summary",
    )
    assert record.target_label == target
    assert target in dispatch_record_markdown(record)


def test_reason_code_ordering_preserved_exactly():
    record = _record()
    assert record.reason_codes == ("decision_approved", "evidence_complete")
    markdown = dispatch_record_markdown(record)
    assert markdown.index("* decision_approved") < markdown.index("* evidence_complete")


def test_dispatch_summary_preserved_exactly():
    summary = "Summary line 1\n\nSummary line 3 with trailing spaces  "
    record = create_dispatch_record(
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        dispatch_type="defer",
        target_label="target",
        execution_mode="manual",
        reason_codes=(),
        source_label="source",
        dispatch_summary=summary,
    )
    assert record.dispatch_summary == summary
    assert summary in dispatch_record_markdown(record)


def test_section_order_never_varies():
    markdown = dispatch_record_markdown(_record())
    sections = [
        "# Dispatch Record",
        "## Dispatch ID",
        "## Human Decision",
        "## Subject",
        "## Subject Kind",
        "## Dispatch Type",
        "## Target",
        "## Execution Mode",
        "## Source",
        "## Reason Codes",
        "## Dispatch Summary",
        "## Provenance Notice",
    ]
    positions = [markdown.index(section) for section in sections]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/dispatch_record/dispatch.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_generated_identifiers():
    first = create_dispatch_record(
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        dispatch_type="implement",
        target_label="target",
        execution_mode="manual",
        reason_codes=(),
        source_label="source",
        dispatch_summary="summary",
    )
    second = create_dispatch_record(
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        subject_id="draft-1",
        subject_kind="prompt_draft",
        dispatch_type="implement",
        target_label="target",
        execution_mode="manual",
        reason_codes=(),
        source_label="source",
        dispatch_summary="summary",
    )
    assert first.dispatch_id == second.dispatch_id == "dispatch-1"


def test_no_tool_calls_or_subprocess():
    source = (_repo_root() / "leira/dispatch_record/dispatch.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_no_ledger_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        dispatch_record_markdown(_record())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        dispatch_record_markdown(_record())
        write_dispatch_record(_record(), tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_dispatcher_imports():
    source = (_repo_root() / "leira/dispatch_record/dispatch.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source


def test_no_workflow_execution_planner_ai_or_approval_logic():
    record = _record()
    assert not hasattr(record, "execute")
    assert not hasattr(record, "run")
    assert not hasattr(record, "plan")
    assert not hasattr(record, "approval")
    assert not hasattr(record, "next_state")


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/dispatch_record/dispatcher.py",
        root / "leira/dispatch_record/workflow.py",
        root / "leira/dispatch_record/approval.py",
        root / "leira/dispatch_record/planner.py",
        root / "leira/dispatch_record/state_machine.py",
        root / "leira/dispatch_record/cli.py",
        root / "leira/dispatch_record/openai.py",
        root / "leira/dispatch_record/claude.py",
        root / "leira/dispatch_record/browser.py",
        root / "leira/dispatch_record/database.py",
        root / "leira/dispatch_record/execution.py",
        root / "leira/dispatch_record/subprocess.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    markdown = dispatch_record_markdown(_record())
    assert PROVENANCE_NOTICE in markdown
