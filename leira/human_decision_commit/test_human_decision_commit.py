from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.engineering_kernel.kernel import run_engineering_kernel
from leira.flow_policy_projection.flow_policy import create_flow_policy, create_flow_rule
from leira.human_decision.decision import HumanDecision
from leira.human_decision_commit.commit import (
    PROVENANCE_NOTICE,
    HumanDecisionCommit,
    commit_human_decision,
    human_decision_commit_markdown,
    write_human_decision_commit,
)
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
                result_action="REQUEST_HUMAN_DECISION",
            ),
        ),
    )


def _draft():
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
    return build_human_decision_draft(result)


def _commit(**overrides):
    defaults = dict(
        decision_id="decision-1",
        source_label="human_operator",
        decision_summary="Approved based on draft recommendation.",
        review_record_ids=("review-1", "review-2"),
        prompt_revision_id="revision-1",
    )
    defaults.update(overrides)
    return commit_human_decision(_draft(), **defaults)


def test_immutable_dataclass():
    commit = _commit()
    with pytest.raises(FrozenInstanceError):
        commit.commit_id = "other"


def test_deterministic_commit_creation():
    draft = _draft()
    kwargs = dict(
        decision_id="decision-1",
        source_label="human_operator",
        decision_summary="Approved based on draft recommendation.",
        review_record_ids=("review-1", "review-2"),
        prompt_revision_id="revision-1",
    )
    first = commit_human_decision(draft, **kwargs)
    second = commit_human_decision(draft, **kwargs)
    assert first == second
    assert isinstance(first, HumanDecisionCommit)


def test_deterministic_markdown():
    commit = _commit()
    first = human_decision_commit_markdown(commit)
    second = human_decision_commit_markdown(commit)
    assert first == second
    assert first.startswith("# Human Decision Commit\n")


def test_byte_identical_repeated_rendering():
    commit = _commit()
    first = human_decision_commit_markdown(commit).encode("utf-8")
    second = human_decision_commit_markdown(commit).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    commit = _commit()
    path = write_human_decision_commit(commit, tmp_path)
    assert path == ".leira/human_decision_commits/decision-1.commit.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == human_decision_commit_markdown(commit)


def test_byte_identical_repeated_writes(tmp_path):
    commit = _commit()
    first_path = write_human_decision_commit(commit, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_human_decision_commit(commit, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_create_human_decision_invoked_exactly_once():
    import leira.human_decision_commit.commit as commit_module

    with patch.object(
        commit_module, "create_human_decision", wraps=commit_module.create_human_decision
    ) as spy:
        _commit()

    assert spy.call_count == 1


def test_human_decision_preserved_exactly():
    draft = _draft()
    commit = commit_human_decision(
        draft,
        decision_id="decision-1",
        source_label="human_operator",
        decision_summary="Approved based on draft recommendation.",
        review_record_ids=("review-1", "review-2"),
        prompt_revision_id="revision-1",
    )
    assert isinstance(commit.human_decision, HumanDecision)
    assert commit.human_decision.decision_id == "decision-1"
    assert commit.human_decision.subject_id == draft.subject_id
    assert commit.human_decision.subject_kind == draft.subject_kind
    assert commit.human_decision.decision_type == draft.recommended_action
    assert commit.human_decision.reason_codes == draft.reason_codes
    assert commit.human_decision.source_label == "human_operator"
    assert commit.human_decision.review_record_ids == ("review-1", "review-2")
    assert commit.human_decision.prompt_revision_id == "revision-1"
    assert commit.human_decision.decision_summary == "Approved based on draft recommendation."


def test_draft_id_preserved_exactly():
    draft = _draft()
    commit = commit_human_decision(
        draft,
        decision_id="decision-1",
        source_label="human_operator",
        decision_summary="summary",
        review_record_ids=(),
        prompt_revision_id="revision-1",
    )
    assert commit.draft_id == draft.subject_id
    assert commit.draft_id == "subject-1"


def test_commit_id_preserved_exactly():
    commit = commit_human_decision(
        _draft(),
        decision_id="decision-xyz",
        source_label="human_operator",
        decision_summary="summary",
        review_record_ids=(),
        prompt_revision_id="revision-1",
    )
    assert commit.commit_id == "decision-xyz"
    assert commit.human_decision.decision_id == "decision-xyz"


def test_section_order_never_varies():
    markdown = human_decision_commit_markdown(_commit())
    markers = [
        "# Human Decision Commit",
        "## Commit ID",
        "## Draft",
        "## Human Decision\n\n```text",
        "## Provenance Notice\n\nThis commit records",
    ]
    positions = [markdown.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/human_decision_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_repository_scanning_or_filesystem_inspection():
    source = (_repo_root() / "leira/human_decision_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("glob", "os.walk", "os.listdir", "scandir", "iterdir", "Path.cwd")
    assert all(term not in source for term in forbidden)


def test_no_ledger_access(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        human_decision_commit_markdown(_commit())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()
    source = (_repo_root() / "leira/human_decision_commit/commit.py").read_text(encoding="utf-8")
    assert "LedgerKernel" not in source
    assert "ledger.connection" not in source
    assert "ledger_events" not in source


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        commit = _commit()
        human_decision_commit_markdown(commit)
        write_human_decision_commit(commit, tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_planner_dispatcher_or_execution():
    source = (_repo_root() / "leira/human_decision_commit/commit.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source
    assert "def plan" not in source
    forbidden = ("planner", "workflow_engine", "def execute")
    assert all(term not in source.lower() for term in forbidden)


def test_no_ai_calls_or_browser_automation():
    source = (_repo_root() / "leira/human_decision_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("openai", "anthropic", "browser", "requests")
    assert all(term not in source.lower() for term in forbidden)


def test_no_subprocess_or_shell_commands():
    source = (_repo_root() / "leira/human_decision_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_no_policy_or_repository_loading():
    source = (_repo_root() / "leira/human_decision_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("load_policy", "open(", "json.load", "yaml.load")
    assert all(term not in source for term in forbidden)


def test_no_automatic_approval_or_automatic_commit():
    source = (_repo_root() / "leira/human_decision_commit/commit.py").read_text(encoding="utf-8")
    forbidden = ("auto_approve", "autocommit", "auto_commit")
    assert all(term not in source.lower() for term in forbidden)
    # commit_human_decision must require an explicit call -- the only
    # occurrence of its name in the source should be its own definition,
    # i.e. nothing in this module invokes it automatically.
    assert source.count("commit_human_decision(") == 1


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/human_decision_commit/dispatcher.py",
        root / "leira/human_decision_commit/planner.py",
        root / "leira/human_decision_commit/workflow.py",
        root / "leira/human_decision_commit/approval.py",
        root / "leira/human_decision_commit/cli.py",
        root / "leira/human_decision_commit/openai.py",
        root / "leira/human_decision_commit/claude.py",
        root / "leira/human_decision_commit/browser.py",
        root / "leira/human_decision_commit/database.py",
        root / "leira/human_decision_commit/loader.py",
        root / "leira/human_decision_commit/execution.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    markdown = human_decision_commit_markdown(_commit())
    assert PROVENANCE_NOTICE in markdown
