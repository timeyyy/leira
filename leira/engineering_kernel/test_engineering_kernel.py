from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.engineering_kernel.kernel import (
    PROVENANCE_NOTICE,
    EngineeringKernelResult,
    engineering_kernel_markdown,
    run_engineering_kernel,
    write_engineering_kernel,
)
from leira.flow_policy_projection.flow_policy import create_flow_policy, create_flow_rule
from leira.inbox.inbox import InboxKernel
from leira.project_state.state import build_project_state


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _policy():
    return create_flow_policy(
        policy_name="default_policy",
        rules=(
            create_flow_rule(
                rule_id="ready_for_decision",
                required_completed=("Prompt Revision",),
                required_missing=("Human Decision",),
                result_action="REQUEST_HUMAN_DECISION",
            ),
            create_flow_rule(
                rule_id="catch_all", required_completed=(), required_missing=(), result_action="WAIT"
            ),
        ),
    )


def _run(**overrides):
    defaults = dict(
        subject_id="subject-1",
        subject_kind="prompt_draft",
        flow_policy=_policy(),
        prompt_draft="x",
        knowledge_gap="x",
        review_question="x",
        review_record="x",
        prompt_revision="x",
    )
    defaults.update(overrides)
    return run_engineering_kernel(**defaults)


def test_immutable_dataclass():
    result = _run()
    with pytest.raises(FrozenInstanceError):
        result.subject_id = "other"


def test_deterministic_execution():
    first = _run()
    second = _run()
    assert first == second
    assert isinstance(first, EngineeringKernelResult)


def test_deterministic_markdown():
    result = _run()
    first = engineering_kernel_markdown(result)
    second = engineering_kernel_markdown(result)
    assert first == second
    assert first.startswith("# Engineering Kernel\n")


def test_byte_identical_repeated_rendering():
    result = _run()
    first = engineering_kernel_markdown(result).encode("utf-8")
    second = engineering_kernel_markdown(result).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    result = _run()
    path = write_engineering_kernel(result, tmp_path)
    assert path == ".leira/engineering_kernel/subject-1.kernel.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == engineering_kernel_markdown(result)


def test_byte_identical_repeated_writes(tmp_path):
    result = _run()
    first_path = write_engineering_kernel(result, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_engineering_kernel(result, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_byte_identical_repeated_execution():
    first = engineering_kernel_markdown(_run())
    second = engineering_kernel_markdown(_run())
    assert first == second


def test_pipeline_order_fixed():
    call_order = []

    def record(name):
        def wrapper(*args, **kwargs):
            call_order.append(name)
            return original[name](*args, **kwargs)

        return wrapper

    import leira.engineering_kernel.kernel as kernel_module

    original = {
        "build_lifecycle_projection": kernel_module.build_lifecycle_projection,
        "build_missing_evidence_projection": kernel_module.build_missing_evidence_projection,
        "evaluate_flow_policy": kernel_module.evaluate_flow_policy,
        "build_engineering_state_projection": kernel_module.build_engineering_state_projection,
    }

    with patch.object(
        kernel_module, "build_lifecycle_projection", side_effect=record("build_lifecycle_projection")
    ), patch.object(
        kernel_module,
        "build_missing_evidence_projection",
        side_effect=record("build_missing_evidence_projection"),
    ), patch.object(
        kernel_module, "evaluate_flow_policy", side_effect=record("evaluate_flow_policy")
    ), patch.object(
        kernel_module,
        "build_engineering_state_projection",
        side_effect=record("build_engineering_state_projection"),
    ):
        _run()

    assert call_order == [
        "build_lifecycle_projection",
        "build_missing_evidence_projection",
        "evaluate_flow_policy",
        "build_engineering_state_projection",
    ]


def test_each_stage_invoked_exactly_once():
    import leira.engineering_kernel.kernel as kernel_module

    with patch.object(
        kernel_module,
        "build_lifecycle_projection",
        wraps=kernel_module.build_lifecycle_projection,
    ) as lifecycle_spy, patch.object(
        kernel_module,
        "build_missing_evidence_projection",
        wraps=kernel_module.build_missing_evidence_projection,
    ) as missing_spy, patch.object(
        kernel_module, "evaluate_flow_policy", wraps=kernel_module.evaluate_flow_policy
    ) as flow_spy, patch.object(
        kernel_module,
        "build_engineering_state_projection",
        wraps=kernel_module.build_engineering_state_projection,
    ) as state_spy:
        _run()

    assert lifecycle_spy.call_count == 1
    assert missing_spy.call_count == 1
    assert flow_spy.call_count == 1
    assert state_spy.call_count == 1


def test_lifecycle_projection_preserved_exactly():
    result = _run()
    assert result.engineering_state_projection.lifecycle_projection == result.lifecycle_projection
    assert result.engineering_state_projection.lifecycle_projection is result.lifecycle_projection


def test_missing_evidence_projection_preserved_exactly():
    result = _run()
    assert (
        result.engineering_state_projection.missing_evidence_projection
        == result.missing_evidence_projection
    )
    assert (
        result.engineering_state_projection.missing_evidence_projection
        is result.missing_evidence_projection
    )


def test_flow_policy_projection_preserved_exactly():
    result = _run()
    assert result.engineering_state_projection.flow_policy_projection == result.flow_policy_projection
    assert result.engineering_state_projection.flow_policy_projection is result.flow_policy_projection


def test_engineering_state_projection_preserved_exactly():
    result = _run()
    assert result.engineering_state_projection.subject_id == result.subject_id
    assert result.engineering_state_projection.subject_kind == result.subject_kind
    assert result.engineering_state_projection.summary.recommended_action == "REQUEST_HUMAN_DECISION"
    assert result.engineering_state_projection.summary.matched_rule == "ready_for_decision"


def test_section_order_never_varies():
    markdown = engineering_kernel_markdown(_run())
    markers = [
        "# Engineering Kernel",
        "## Subject\n\nSubject ID:",
        "## Lifecycle\n\n```text",
        "## Missing Evidence\n\n```text",
        "## Flow Policy\n\n```text",
        "## Engineering State\n\n```text",
        "## Provenance Notice\n\nThis result composes",
    ]
    positions = [markdown.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/engineering_kernel/kernel.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_repository_scanning_or_filesystem_inspection():
    source = (_repo_root() / "leira/engineering_kernel/kernel.py").read_text(encoding="utf-8")
    forbidden = ("glob", "os.walk", "os.listdir", "scandir", "iterdir", "Path.cwd")
    assert all(term not in source for term in forbidden)


def test_no_ledger_access(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        engineering_kernel_markdown(_run())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()
    source = (_repo_root() / "leira/engineering_kernel/kernel.py").read_text(encoding="utf-8")
    assert "LedgerKernel" not in source
    assert "ledger.connection" not in source
    assert "ledger_events" not in source


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        result = _run()
        engineering_kernel_markdown(result)
        write_engineering_kernel(result, tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_planner_dispatcher_or_execution():
    source = (_repo_root() / "leira/engineering_kernel/kernel.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source
    assert "def plan" not in source
    forbidden = ("planner", "workflow_engine", "def execute", "subprocess.run")
    assert all(term not in source.lower() for term in forbidden)


def test_no_ai_calls_or_browser_automation():
    source = (_repo_root() / "leira/engineering_kernel/kernel.py").read_text(encoding="utf-8")
    forbidden = ("openai", "anthropic", "browser", "requests")
    assert all(term not in source.lower() for term in forbidden)


def test_no_subprocess_or_shell_commands():
    source = (_repo_root() / "leira/engineering_kernel/kernel.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_no_policy_loading_or_repository_loading():
    source = (_repo_root() / "leira/engineering_kernel/kernel.py").read_text(encoding="utf-8")
    forbidden = ("load_policy", "open(", "json.load", "yaml.load")
    assert all(term not in source for term in forbidden)


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/engineering_kernel/dispatcher.py",
        root / "leira/engineering_kernel/planner.py",
        root / "leira/engineering_kernel/workflow.py",
        root / "leira/engineering_kernel/approval.py",
        root / "leira/engineering_kernel/rejection.py",
        root / "leira/engineering_kernel/cli.py",
        root / "leira/engineering_kernel/openai.py",
        root / "leira/engineering_kernel/claude.py",
        root / "leira/engineering_kernel/browser.py",
        root / "leira/engineering_kernel/database.py",
        root / "leira/engineering_kernel/loader.py",
        root / "leira/engineering_kernel/execution.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    markdown = engineering_kernel_markdown(_run())
    assert PROVENANCE_NOTICE in markdown
