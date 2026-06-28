from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatch_draft.draft import (
    PENDING_EXECUTION_MODE,
    PENDING_TARGET_LABEL,
    PROVENANCE_NOTICE,
    DispatchDraft,
    build_dispatch_draft,
    dispatch_draft_markdown,
    write_dispatch_draft,
)
from leira.dispatcher.kernel import LedgerKernel
from leira.engineering_kernel.kernel import run_engineering_kernel
from leira.flow_policy_projection.flow_policy import create_flow_policy, create_flow_rule
from leira.human_decision_commit.commit import commit_human_decision
from leira.human_decision_draft.draft import build_human_decision_draft
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
                result_action="implement",
            ),
        ),
    )


def _commit(**overrides):
    result = run_engineering_kernel(
        subject_id="subject-1",
        subject_kind="prompt_draft",
        flow_policy=_policy(),
        prompt_draft="x",
        knowledge_gap="x",
        review_question="x",
        review_record="x",
        prompt_revision="x",
    )
    human_decision_draft = build_human_decision_draft(result)
    defaults = dict(
        decision_id="decision-1",
        source_label="human_operator",
        decision_summary="Approved based on draft recommendation.",
        review_record_ids=("review-1",),
        prompt_revision_id="revision-1",
    )
    defaults.update(overrides)
    return commit_human_decision(human_decision_draft, **defaults)


def _draft(**overrides):
    return build_dispatch_draft(_commit(**overrides))


def test_immutable_dataclass():
    draft = _draft()
    with pytest.raises(FrozenInstanceError):
        draft.dispatch_type = "other"


def test_deterministic_draft_creation():
    commit = _commit()
    first = build_dispatch_draft(commit)
    second = build_dispatch_draft(commit)
    assert first == second
    assert isinstance(first, DispatchDraft)


def test_deterministic_markdown():
    draft = _draft()
    first = dispatch_draft_markdown(draft)
    second = dispatch_draft_markdown(draft)
    assert first == second
    assert first.startswith("# Dispatch Draft\n")


def test_byte_identical_repeated_rendering():
    draft = _draft()
    first = dispatch_draft_markdown(draft).encode("utf-8")
    second = dispatch_draft_markdown(draft).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    draft = _draft()
    path = write_dispatch_draft(draft, tmp_path)
    assert path == ".leira/dispatch_drafts/subject-1.draft.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == dispatch_draft_markdown(draft)


def test_byte_identical_repeated_writes(tmp_path):
    draft = _draft()
    first_path = write_dispatch_draft(draft, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_dispatch_draft(draft, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_dispatch_type_preserved_exactly():
    commit = _commit()
    draft = build_dispatch_draft(commit)
    assert draft.dispatch_type == commit.human_decision.decision_type
    assert draft.dispatch_type == "implement"
    assert draft.dispatch_type in dispatch_draft_markdown(draft)


def test_target_label_preserved_exactly():
    draft = _draft()
    assert draft.target_label == PENDING_TARGET_LABEL
    assert draft.target_label in dispatch_draft_markdown(draft)


def test_execution_mode_preserved_exactly():
    draft = _draft()
    assert draft.execution_mode == PENDING_EXECUTION_MODE
    assert draft.execution_mode in dispatch_draft_markdown(draft)


def test_reason_codes_preserved_exactly():
    commit = _commit()
    draft = build_dispatch_draft(commit)
    assert draft.reason_codes == commit.human_decision.reason_codes
    assert draft.reason_codes == ("ready_for_decision: MATCHED",)
    markdown = dispatch_draft_markdown(draft)
    for entry in draft.reason_codes:
        assert f"* {entry}" in markdown


def test_draft_summary_deterministic():
    commit = _commit()
    first = build_dispatch_draft(commit).draft_summary
    second = build_dispatch_draft(commit).draft_summary
    assert first == second
    assert "implement" in first
    assert "subject-1" in first


def test_subject_id_and_kind_copied_from_human_decision():
    commit = _commit()
    draft = build_dispatch_draft(commit)
    assert draft.subject_id == commit.human_decision.subject_id
    assert draft.subject_kind == commit.human_decision.subject_kind


def test_section_order_never_varies():
    markdown = dispatch_draft_markdown(_draft())
    sections = [
        "# Dispatch Draft",
        "## Subject",
        "## Dispatch Type",
        "## Target",
        "## Execution Mode",
        "## Reason Codes",
        "## Draft Summary",
        "## Provenance Notice",
    ]
    positions = [markdown.index(section) for section in sections]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/dispatch_draft/draft.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_repository_scanning_or_filesystem_inspection():
    source = (_repo_root() / "leira/dispatch_draft/draft.py").read_text(encoding="utf-8")
    forbidden = ("glob", "os.walk", "os.listdir", "scandir", "iterdir", "Path.cwd")
    assert all(term not in source for term in forbidden)


def test_no_ledger_access(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        dispatch_draft_markdown(_draft())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()
    source = (_repo_root() / "leira/dispatch_draft/draft.py").read_text(encoding="utf-8")
    assert "LedgerKernel" not in source
    assert "ledger.connection" not in source
    assert "ledger_events" not in source


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        draft = _draft()
        dispatch_draft_markdown(draft)
        write_dispatch_draft(draft, tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_dispatch_record_creation():
    source = (_repo_root() / "leira/dispatch_draft/draft.py").read_text(encoding="utf-8")
    assert "create_dispatch_record" not in source
    assert "DispatchRecord(" not in source
    assert "write_dispatch_record" not in source


def test_no_planner_dispatcher_or_execution():
    source = (_repo_root() / "leira/dispatch_draft/draft.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source
    assert "def plan" not in source
    forbidden = ("planner", "workflow_engine", "def execute")
    assert all(term not in source.lower() for term in forbidden)


def test_no_ai_calls_or_browser_automation():
    source = (_repo_root() / "leira/dispatch_draft/draft.py").read_text(encoding="utf-8")
    forbidden = ("openai", "anthropic", "browser", "requests")
    assert all(term not in source.lower() for term in forbidden)


def test_no_subprocess_or_shell_commands():
    source = (_repo_root() / "leira/dispatch_draft/draft.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_no_policy_or_repository_loading():
    source = (_repo_root() / "leira/dispatch_draft/draft.py").read_text(encoding="utf-8")
    forbidden = ("load_policy", "open(", "json.load", "yaml.load")
    assert all(term not in source for term in forbidden)


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/dispatch_draft/dispatcher.py",
        root / "leira/dispatch_draft/planner.py",
        root / "leira/dispatch_draft/workflow.py",
        root / "leira/dispatch_draft/approval.py",
        root / "leira/dispatch_draft/cli.py",
        root / "leira/dispatch_draft/openai.py",
        root / "leira/dispatch_draft/claude.py",
        root / "leira/dispatch_draft/browser.py",
        root / "leira/dispatch_draft/database.py",
        root / "leira/dispatch_draft/loader.py",
        root / "leira/dispatch_draft/execution.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    markdown = dispatch_draft_markdown(_draft())
    assert PROVENANCE_NOTICE in markdown
