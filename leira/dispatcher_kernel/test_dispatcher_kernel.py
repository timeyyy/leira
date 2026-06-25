from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatch_record.dispatch import DispatchRecord, create_dispatch_record
from leira.dispatcher_kernel.dispatcher import (
    PROVENANCE_NOTICE,
    DispatchPlan,
    DispatcherKernelResult,
    build_dispatch_plan,
    run_dispatcher_kernel,
    dispatcher_kernel_markdown,
    write_dispatcher_kernel,
)
from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.project_state.state import build_project_state


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _record(**overrides) -> DispatchRecord:
    defaults = dict(
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        subject_id="subject-1",
        subject_kind="prompt_draft",
        dispatch_type="implement",
        target_label="worker_pool_a",
        execution_mode="manual",
        reason_codes=("decision_approved", "evidence_complete"),
        source_label="human_operator",
        dispatch_summary="Dispatch summary description.",
    )
    defaults.update(overrides)
    return create_dispatch_record(**defaults)


def test_immutable_dataclasses():
    record = _record()
    result = run_dispatcher_kernel(record)
    plan = result.dispatch_plan

    with pytest.raises(FrozenInstanceError):
        plan.dispatch_id = "other"

    with pytest.raises(FrozenInstanceError):
        result.dispatch_plan = plan


def test_deterministic_plan_creation():
    record = _record()
    first = build_dispatch_plan(record)
    second = build_dispatch_plan(record)
    assert first == second
    assert isinstance(first, DispatchPlan)


def test_deterministic_kernel_execution():
    record = _record()
    first = run_dispatcher_kernel(record)
    second = run_dispatcher_kernel(record)
    assert first == second
    assert isinstance(first, DispatcherKernelResult)


def test_deterministic_markdown():
    record = _record()
    result = run_dispatcher_kernel(record)
    first = dispatcher_kernel_markdown(result)
    second = dispatcher_kernel_markdown(result)
    assert first == second
    assert first.startswith("# Dispatcher Kernel\n")

    # Verify section order
    markers = [
        "# Dispatcher Kernel",
        "## Dispatch Record\n\n```text",
        "## Dispatch Plan\n\n```text",
        "## Provenance Notice\n\nThis kernel prepares",
    ]
    positions = [first.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_deterministic_file_output(tmp_path):
    record = _record()
    result = run_dispatcher_kernel(record)
    path = write_dispatcher_kernel(result, tmp_path)
    assert path == ".leira/dispatcher_kernel/dispatch-1.dispatch_plan.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == dispatcher_kernel_markdown(result)


def test_dispatch_record_preserved_exactly():
    record = _record()
    result = run_dispatcher_kernel(record)
    assert result.dispatch_record == record


def test_dispatch_plan_preserved_exactly():
    record = _record()
    result = run_dispatcher_kernel(record)
    plan = result.dispatch_plan
    assert plan.dispatch_id == record.dispatch_id
    assert plan.subject_id == record.subject_id
    assert plan.subject_kind == record.subject_kind
    assert plan.dispatch_type == record.dispatch_type
    assert plan.target_label == record.target_label
    assert plan.execution_mode == record.execution_mode
    assert plan.reason_codes == record.reason_codes
    assert plan.dispatch_summary == record.dispatch_summary


def test_byte_identical_repeated_rendering():
    record = _record()
    result = run_dispatcher_kernel(record)
    first = dispatcher_kernel_markdown(result).encode("utf-8")
    second = dispatcher_kernel_markdown(result).encode("utf-8")
    assert first == second


def test_byte_identical_repeated_writes(tmp_path):
    record = _record()
    result = run_dispatcher_kernel(record)
    first_path = write_dispatcher_kernel(result, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_dispatcher_kernel(result, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_byte_identical_repeated_execution():
    record = _record()
    first = run_dispatcher_kernel(record)
    second = run_dispatcher_kernel(record)
    assert first == second


def test_validation_errors():
    with pytest.raises(TypeError):
        run_dispatcher_kernel("not-a-record")

    # Test invalid string types
    with pytest.raises(TypeError):
        run_dispatcher_kernel(_record(dispatch_id=123))

    # Test empty values
    with pytest.raises(ValueError):
        run_dispatcher_kernel(_record(dispatch_id=""))

    with pytest.raises(ValueError):
        run_dispatcher_kernel(_record(dispatch_id="   "))

    # Test invalid reason_codes container
    with pytest.raises(TypeError):
        run_dispatcher_kernel(DispatchRecord(
            dispatch_id="dispatch-1",
            human_decision_id="decision-1",
            subject_id="subject-1",
            subject_kind="prompt_draft",
            dispatch_type="implement",
            target_label="worker_pool_a",
            execution_mode="manual",
            reason_codes=["not-a-tuple"],
            source_label="human_operator",
            dispatch_summary="Dispatch summary description.",
        ))

    # Test invalid reason code elements
    with pytest.raises(TypeError):
        run_dispatcher_kernel(_record(reason_codes=(123,)))


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/dispatcher_kernel/dispatcher.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_repository_scanning_or_filesystem_inspection():
    source = (_repo_root() / "leira/dispatcher_kernel/dispatcher.py").read_text(encoding="utf-8")
    forbidden = ("glob", "os.walk", "os.listdir", "scandir", "iterdir", "Path.cwd")
    assert all(term not in source for term in forbidden)


def test_no_ledger_access(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
        run_dispatcher_kernel(_record())
        assert ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall() == before
    finally:
        ledger.close()

    source = (_repo_root() / "leira/dispatcher_kernel/dispatcher.py").read_text(encoding="utf-8")
    assert "LedgerKernel" not in source
    assert "ledger.connection" not in source
    assert "ledger_events" not in source


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        result = run_dispatcher_kernel(_record())
        write_dispatcher_kernel(result, tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_planner_or_execution():
    source = (_repo_root() / "leira/dispatcher_kernel/dispatcher.py").read_text(encoding="utf-8")
    # Clean docstrings and PROVENANCE_NOTICE definition to avoid matching required notice words
    lines = [
        line for line in source.splitlines()
        if not line.strip().startswith("#")
        and "PROVENANCE_NOTICE =" not in line
        and '"""' not in line
        and 'It performs no' not in line
        and 'It never performs' not in line
    ]
    cleaned_source = "\n".join(lines).lower()

    assert "def plan" not in cleaned_source
    assert "def execute" not in cleaned_source
    forbidden = ("planner", "workflow_engine", "scheduling", "worker")
    assert all(term not in cleaned_source for term in forbidden)


def test_no_ai_calls_or_browser_automation():
    source = (_repo_root() / "leira/dispatcher_kernel/dispatcher.py").read_text(encoding="utf-8")
    forbidden = ("openai", "anthropic", "browser", "requests", "urllib")
    assert all(term not in source.lower() for term in forbidden)


def test_no_subprocess_or_shell_commands():
    source = (_repo_root() / "leira/dispatcher_kernel/dispatcher.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_provenance_notice_present():
    record = _record()
    result = run_dispatcher_kernel(record)
    markdown = dispatcher_kernel_markdown(result)
    assert PROVENANCE_NOTICE in markdown
