from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.engineering_kernel.kernel import run_engineering_kernel
from leira.flow_policy_projection.flow_policy import create_flow_policy, create_flow_rule
from leira.human_decision_draft.draft import (
    PROVENANCE_NOTICE,
    HumanDecisionDraft,
    build_human_decision_draft,
    human_decision_draft_markdown,
    write_human_decision_draft,
)
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


def _result(**overrides):
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


def _draft(**overrides):
    return build_human_decision_draft(_result(**overrides))


def _no_match_result():
    return _result(
        flow_policy=create_flow_policy(
            policy_name="strict",
            rules=(
                create_flow_rule(
                    rule_id="ready_for_decision",
                    required_completed=("Prompt Revision",),
                    required_missing=("Human Decision",),
                    result_action="REQUEST_HUMAN_DECISION",
                ),
            ),
        ),
        prompt_revision=None,
    )


def test_immutable_dataclass():
    draft = _draft()
    with pytest.raises(FrozenInstanceError):
        draft.recommended_action = "OTHER"


def test_deterministic_draft_creation():
    result = _result()
    first = build_human_decision_draft(result)
    second = build_human_decision_draft(result)
    assert first == second
    assert isinstance(first, HumanDecisionDraft)


def test_deterministic_markdown():
    draft = _draft()
    first = human_decision_draft_markdown(draft)
    second = human_decision_draft_markdown(draft)
    assert first == second
    assert first.startswith("# Human Decision Draft\n")


def test_byte_identical_repeated_rendering():
    draft = _draft()
    first = human_decision_draft_markdown(draft).encode("utf-8")
    second = human_decision_draft_markdown(draft).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    draft = _draft()
    path = write_human_decision_draft(draft, tmp_path)
    assert path == ".leira/human_decision_drafts/subject-1.draft.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == human_decision_draft_markdown(draft)


def test_byte_identical_repeated_writes(tmp_path):
    draft = _draft()
    first_path = write_human_decision_draft(draft, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_human_decision_draft(draft, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_recommended_action_copied_exactly():
    result = _result()
    draft = build_human_decision_draft(result)
    assert draft.recommended_action == result.flow_policy_projection.recommended_action
    assert draft.recommended_action == "REQUEST_HUMAN_DECISION"
    assert draft.recommended_action in human_decision_draft_markdown(draft)


def test_matched_rule_copied_exactly():
    result = _result()
    draft = build_human_decision_draft(result)
    assert draft.matched_rule == result.flow_policy_projection.matched_rule_id
    assert draft.matched_rule == "ready_for_decision"
    assert draft.matched_rule in human_decision_draft_markdown(draft)


def test_matched_rule_none_rendered_as_none():
    result = _no_match_result()
    draft = build_human_decision_draft(result)
    assert draft.matched_rule is None
    assert draft.recommended_action == "NO_MATCH"
    markdown = human_decision_draft_markdown(draft)
    assert "## Matched Rule\n\nNone" in markdown


def test_reason_codes_preserved_exactly():
    result = _result()
    draft = build_human_decision_draft(result)
    assert draft.reason_codes == result.flow_policy_projection.evaluation_trace
    assert draft.reason_codes == ("ready_for_decision: MATCHED",)
    markdown = human_decision_draft_markdown(draft)
    for entry in draft.reason_codes:
        assert f"* {entry}" in markdown


def test_reason_codes_ordering_preserved_for_skipped_then_matched():
    result = _no_match_result()
    # ready_for_decision required Prompt Revision which is absent, so the
    # rule is skipped and overall evaluation reports no match.
    draft = build_human_decision_draft(result)
    assert draft.reason_codes == ("ready_for_decision: SKIPPED",)


def test_draft_summary_deterministic():
    result = _result()
    first = build_human_decision_draft(result).draft_summary
    second = build_human_decision_draft(result).draft_summary
    assert first == second
    assert "REQUEST_HUMAN_DECISION" in first
    assert "subject-1" in first
    assert "ready_for_decision" in first


def test_section_order_never_varies():
    markdown = human_decision_draft_markdown(_draft())
    sections = [
        "# Human Decision Draft",
        "## Subject",
        "## Recommended Action",
        "## Matched Rule",
        "## Reason Codes",
        "## Draft Summary",
        "## Provenance Notice",
    ]
    positions = [markdown.index(section) for section in sections]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/human_decision_draft/draft.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_repository_scanning_or_filesystem_inspection():
    source = (_repo_root() / "leira/human_decision_draft/draft.py").read_text(encoding="utf-8")
    forbidden = ("glob", "os.walk", "os.listdir", "scandir", "iterdir", "Path.cwd")
    assert all(term not in source for term in forbidden)


def test_no_ledger_access(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        human_decision_draft_markdown(_draft())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()
    source = (_repo_root() / "leira/human_decision_draft/draft.py").read_text(encoding="utf-8")
    assert "LedgerKernel" not in source
    assert "ledger.connection" not in source
    assert "ledger_events" not in source


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        draft = _draft()
        human_decision_draft_markdown(draft)
        write_human_decision_draft(draft, tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_human_decision_creation_planner_dispatcher_or_execution():
    source = (_repo_root() / "leira/human_decision_draft/draft.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source
    assert "create_human_decision" not in source
    assert "HumanDecision(" not in source
    forbidden = ("planner", "workflow_engine", "def execute")
    assert all(term not in source.lower() for term in forbidden)


def test_no_ai_calls_or_browser_automation():
    source = (_repo_root() / "leira/human_decision_draft/draft.py").read_text(encoding="utf-8")
    forbidden = ("openai", "anthropic", "browser", "requests")
    assert all(term not in source.lower() for term in forbidden)


def test_no_subprocess_or_shell_commands():
    source = (_repo_root() / "leira/human_decision_draft/draft.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_no_policy_or_repository_loading():
    source = (_repo_root() / "leira/human_decision_draft/draft.py").read_text(encoding="utf-8")
    forbidden = ("load_policy", "open(", "json.load", "yaml.load")
    assert all(term not in source for term in forbidden)


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/human_decision_draft/dispatcher.py",
        root / "leira/human_decision_draft/planner.py",
        root / "leira/human_decision_draft/workflow.py",
        root / "leira/human_decision_draft/approval.py",
        root / "leira/human_decision_draft/rejection.py",
        root / "leira/human_decision_draft/cli.py",
        root / "leira/human_decision_draft/openai.py",
        root / "leira/human_decision_draft/claude.py",
        root / "leira/human_decision_draft/browser.py",
        root / "leira/human_decision_draft/database.py",
        root / "leira/human_decision_draft/loader.py",
        root / "leira/human_decision_draft/execution.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    markdown = human_decision_draft_markdown(_draft())
    assert PROVENANCE_NOTICE in markdown
