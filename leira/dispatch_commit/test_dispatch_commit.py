from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

from leira.dispatch_commit.commit import (
    PROVENANCE_NOTICE,
    DispatchCommit,
    commit_dispatch,
    dispatch_commit_markdown,
    write_dispatch_commit,
)
from leira.dispatch_draft.draft import build_dispatch_draft
from leira.dispatch_record.dispatch import DispatchRecord
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


def _dispatch_draft():
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
    human_decision_commit = commit_human_decision(
        human_decision_draft,
        decision_id="decision-1",
        source_label="human_operator",
        decision_summary="Approved based on draft recommendation.",
        review_record_ids=("review-1",),
        prompt_revision_id="revision-1",
    )
    return build_dispatch_draft(human_decision_commit)


def _commit(**overrides):
    defaults = dict(
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        source_label="human_operator",
        dispatch_summary="Dispatching to worker pool A.",
        target_label="worker_pool_a",
        execution_mode="manual",
    )
    defaults.update(overrides)
    return commit_dispatch(_dispatch_draft(), **defaults)


def test_immutable_dataclass():
    commit = _commit()
    with pytest.raises(FrozenInstanceError):
        commit.commit_id = "other"


def test_deterministic_commit_creation():
    draft = _dispatch_draft()
    kwargs = dict(
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        source_label="human_operator",
        dispatch_summary="Dispatching to worker pool A.",
        target_label="worker_pool_a",
        execution_mode="manual",
    )
    first = commit_dispatch(draft, **kwargs)
    second = commit_dispatch(draft, **kwargs)
    assert first == second
    assert isinstance(first, DispatchCommit)


def test_deterministic_markdown():
    commit = _commit()
    first = dispatch_commit_markdown(commit)
    second = dispatch_commit_markdown(commit)
    assert first == second
    assert first.startswith("# Dispatch Commit\n")


def test_byte_identical_repeated_rendering():
    commit = _commit()
    first = dispatch_commit_markdown(commit).encode("utf-8")
    second = dispatch_commit_markdown(commit).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    commit = _commit()
    path = write_dispatch_commit(commit, tmp_path)
    assert path == ".leira/dispatch_commits/dispatch-1.commit.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == dispatch_commit_markdown(commit)


def test_byte_identical_repeated_writes(tmp_path):
    commit = _commit()
    first_path = write_dispatch_commit(commit, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_dispatch_commit(commit, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_create_dispatch_record_invoked_exactly_once():
    import leira.dispatch_commit.commit as commit_module

    with patch.object(
        commit_module, "create_dispatch_record", wraps=commit_module.create_dispatch_record
    ) as spy:
        _commit()

    assert spy.call_count == 1


def test_dispatch_record_preserved_exactly():
    draft = _dispatch_draft()
    commit = commit_dispatch(
        draft,
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        source_label="human_operator",
        dispatch_summary="Dispatching to worker pool A.",
        target_label="worker_pool_a",
        execution_mode="manual",
    )
    assert isinstance(commit.dispatch_record, DispatchRecord)
    assert commit.dispatch_record.dispatch_id == "dispatch-1"
    assert commit.dispatch_record.human_decision_id == "decision-1"
    assert commit.dispatch_record.subject_id == draft.subject_id
    assert commit.dispatch_record.subject_kind == draft.subject_kind
    assert commit.dispatch_record.dispatch_type == draft.dispatch_type
    assert commit.dispatch_record.target_label == "worker_pool_a"
    assert commit.dispatch_record.execution_mode == "manual"
    assert commit.dispatch_record.reason_codes == draft.reason_codes
    assert commit.dispatch_record.source_label == "human_operator"
    assert commit.dispatch_record.dispatch_summary == "Dispatching to worker pool A."


def test_draft_id_preserved_exactly():
    draft = _dispatch_draft()
    commit = commit_dispatch(
        draft,
        dispatch_id="dispatch-1",
        human_decision_id="decision-1",
        source_label="human_operator",
        dispatch_summary="summary",
        target_label="target",
        execution_mode="manual",
    )
    assert commit.draft_id == draft.subject_id
    assert commit.draft_id == "subject-1"


def test_commit_id_preserved_exactly():
    commit = commit_dispatch(
        _dispatch_draft(),
        dispatch_id="dispatch-xyz",
        human_decision_id="decision-1",
        source_label="human_operator",
        dispatch_summary="summary",
        target_label="target",
        execution_mode="manual",
    )
    assert commit.commit_id == "dispatch-xyz"
    assert commit.dispatch_record.dispatch_id == "dispatch-xyz"


def test_section_order_never_varies():
    markdown = dispatch_commit_markdown(_commit())
    markers = [
        "# Dispatch Commit",
        "## Commit ID",
        "## Draft",
        "## Dispatch Record\n\n```text",
        "## Provenance Notice\n\nThis commit records",
    ]
    positions = [markdown.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/dispatch_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_repository_scanning_or_filesystem_inspection():
    source = (_repo_root() / "leira/dispatch_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("glob", "os.walk", "os.listdir", "scandir", "iterdir", "Path.cwd")
    assert all(term not in source for term in forbidden)


def test_no_ledger_access(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        dispatch_commit_markdown(_commit())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()
    source = (_repo_root() / "leira/dispatch_commit/commit.py").read_text(encoding="utf-8")
    assert "LedgerKernel" not in source
    assert "ledger.connection" not in source
    assert "ledger_events" not in source


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        commit = _commit()
        dispatch_commit_markdown(commit)
        write_dispatch_commit(commit, tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_planner_dispatcher_or_execution():
    source = (_repo_root() / "leira/dispatch_commit/commit.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source
    assert "def plan" not in source
    forbidden = ("planner", "workflow_engine", "def execute")
    assert all(term not in source.lower() for term in forbidden)


def test_no_ai_calls_or_browser_automation():
    source = (_repo_root() / "leira/dispatch_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("openai", "anthropic", "browser", "requests")
    assert all(term not in source.lower() for term in forbidden)


def test_no_subprocess_or_shell_commands():
    source = (_repo_root() / "leira/dispatch_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_no_policy_or_repository_loading():
    source = (_repo_root() / "leira/dispatch_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("load_policy", "open(", "json.load", "yaml.load")
    assert all(term not in source for term in forbidden)


def test_no_automatic_dispatch_or_automatic_commit():
    source = (_repo_root() / "leira/dispatch_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("auto_dispatch", "autocommit", "auto_commit")
    assert all(term not in source.lower() for term in forbidden)
    # commit_dispatch must require an explicit call -- the only occurrence
    # of its name in the source should be its own definition, i.e.
    # nothing in this module invokes it automatically.
    assert source.count("commit_dispatch(") == 1


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/dispatch_commit/dispatcher.py",
        root / "leira/dispatch_commit/planner.py",
        root / "leira/dispatch_commit/workflow.py",
        root / "leira/dispatch_commit/approval.py",
        root / "leira/dispatch_commit/cli.py",
        root / "leira/dispatch_commit/openai.py",
        root / "leira/dispatch_commit/claude.py",
        root / "leira/dispatch_commit/browser.py",
        root / "leira/dispatch_commit/database.py",
        root / "leira/dispatch_commit/loader.py",
        root / "leira/dispatch_commit/execution.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    markdown = dispatch_commit_markdown(_commit())
    assert PROVENANCE_NOTICE in markdown
