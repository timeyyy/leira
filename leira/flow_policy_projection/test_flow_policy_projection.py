from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.flow_policy_projection.flow_policy import (
    NO_MATCH,
    PROVENANCE_NOTICE,
    FlowPolicy,
    FlowPolicyProjection,
    FlowRule,
    create_flow_policy,
    create_flow_rule,
    evaluate_flow_policy,
    flow_policy_projection_markdown,
    write_flow_policy_projection,
)
from leira.inbox.inbox import InboxKernel
from leira.lifecycle_projection.lifecycle import build_lifecycle_projection
from leira.project_state.state import build_project_state


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _lifecycle_projection(**overrides):
    defaults = dict(
        subject_id="subject-1",
        subject_kind="prompt_draft",
        prompt_draft="x",
        knowledge_gap="x",
        review_question="x",
        review_record="x",
        prompt_revision="x",
    )
    defaults.update(overrides)
    return build_lifecycle_projection(**defaults)


def _rule_a():
    return create_flow_rule(
        rule_id="ready_for_decision",
        required_completed=("Prompt Revision",),
        required_missing=("Human Decision",),
        result_action="REQUEST_HUMAN_DECISION",
    )


def _rule_b():
    return create_flow_rule(
        rule_id="catch_all",
        required_completed=(),
        required_missing=(),
        result_action="WAIT",
    )


def _policy():
    return create_flow_policy(policy_name="default_policy", rules=(_rule_a(), _rule_b()))


def test_flow_rule_immutable():
    rule = _rule_a()
    with pytest.raises(FrozenInstanceError):
        rule.result_action = "OTHER"


def test_flow_policy_immutable():
    policy = _policy()
    with pytest.raises(FrozenInstanceError):
        policy.policy_name = "other"


def test_flow_policy_projection_immutable():
    projection = evaluate_flow_policy(lifecycle_projection=_lifecycle_projection(), flow_policy=_policy())
    with pytest.raises(FrozenInstanceError):
        projection.recommended_action = "OTHER"


def test_deterministic_policy_creation():
    assert _policy() == _policy()
    assert isinstance(_policy(), FlowPolicy)


def test_deterministic_rule_creation():
    assert _rule_a() == _rule_a()
    assert isinstance(_rule_a(), FlowRule)


def test_deterministic_evaluation():
    lp = _lifecycle_projection()
    policy = _policy()
    first = evaluate_flow_policy(lifecycle_projection=lp, flow_policy=policy)
    second = evaluate_flow_policy(lifecycle_projection=lp, flow_policy=policy)
    assert first == second
    assert isinstance(first, FlowPolicyProjection)


def test_deterministic_markdown():
    projection = evaluate_flow_policy(lifecycle_projection=_lifecycle_projection(), flow_policy=_policy())
    first = flow_policy_projection_markdown(projection)
    second = flow_policy_projection_markdown(projection)
    assert first == second
    assert first.startswith("# Flow Policy Projection\n")


def test_byte_identical_repeated_rendering():
    projection = evaluate_flow_policy(lifecycle_projection=_lifecycle_projection(), flow_policy=_policy())
    first = flow_policy_projection_markdown(projection).encode("utf-8")
    second = flow_policy_projection_markdown(projection).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    projection = evaluate_flow_policy(lifecycle_projection=_lifecycle_projection(), flow_policy=_policy())
    path = write_flow_policy_projection(projection, tmp_path)
    assert path == ".leira/flow_policy/subject-1.flow.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == flow_policy_projection_markdown(projection)


def test_byte_identical_repeated_writes(tmp_path):
    projection = evaluate_flow_policy(lifecycle_projection=_lifecycle_projection(), flow_policy=_policy())
    first_path = write_flow_policy_projection(projection, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_flow_policy_projection(projection, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_first_matching_rule_wins():
    lp = _lifecycle_projection()
    policy = _policy()
    projection = evaluate_flow_policy(lifecycle_projection=lp, flow_policy=policy)
    assert projection.matched_rule_id == "ready_for_decision"
    assert projection.recommended_action == "REQUEST_HUMAN_DECISION"


def test_first_matching_rule_wins_even_if_later_rule_also_matches():
    lp = _lifecycle_projection()
    catch_all_first = create_flow_rule(
        rule_id="catch_all", required_completed=(), required_missing=(), result_action="WAIT"
    )
    policy = create_flow_policy(policy_name="reordered", rules=(catch_all_first, _rule_a()))
    projection = evaluate_flow_policy(lifecycle_projection=lp, flow_policy=policy)
    assert projection.matched_rule_id == "catch_all"
    assert projection.recommended_action == "WAIT"


def test_no_matching_rule_returns_no_match():
    lp = _lifecycle_projection(human_decision="x")
    policy = create_flow_policy(policy_name="strict", rules=(_rule_a(),))
    projection = evaluate_flow_policy(lifecycle_projection=lp, flow_policy=policy)
    assert projection.matched_rule_id is None
    assert projection.recommended_action == NO_MATCH
    assert projection.recommended_action == "NO_MATCH"
    markdown = flow_policy_projection_markdown(projection)
    assert "None" in markdown
    assert "NO_MATCH" in markdown


def test_empty_policy_returns_no_match():
    lp = _lifecycle_projection()
    policy = create_flow_policy(policy_name="empty", rules=())
    projection = evaluate_flow_policy(lifecycle_projection=lp, flow_policy=policy)
    assert projection.matched_rule_id is None
    assert projection.recommended_action == NO_MATCH
    assert projection.evaluation_trace == ()


def test_evaluation_trace_preserved_for_match_after_skips():
    lp = _lifecycle_projection(human_decision="x")
    skip_rule = create_flow_rule(
        rule_id="needs_no_decision",
        required_completed=(),
        required_missing=("Human Decision",),
        result_action="REQUEST_HUMAN_DECISION",
    )
    catch_all = _rule_b()
    policy = create_flow_policy(policy_name="ordered", rules=(skip_rule, catch_all))
    projection = evaluate_flow_policy(lifecycle_projection=lp, flow_policy=policy)
    assert projection.evaluation_trace == ("needs_no_decision: SKIPPED", "catch_all: MATCHED")
    markdown = flow_policy_projection_markdown(projection)
    assert markdown.index("* needs_no_decision: SKIPPED") < markdown.index("* catch_all: MATCHED")


def test_evaluation_trace_preserved_for_no_match():
    lp = _lifecycle_projection(human_decision="x")
    policy = create_flow_policy(policy_name="strict", rules=(_rule_a(), _rule_a()))
    projection = evaluate_flow_policy(lifecycle_projection=lp, flow_policy=policy)
    assert projection.evaluation_trace == (
        "ready_for_decision: SKIPPED",
        "ready_for_decision: SKIPPED",
    )


def test_caller_rule_ordering_preserved_in_policy():
    rule_x = create_flow_rule(rule_id="x", required_completed=(), required_missing=(), result_action="X")
    rule_y = create_flow_rule(rule_id="y", required_completed=(), required_missing=(), result_action="Y")
    policy = create_flow_policy(policy_name="ordered", rules=(rule_y, rule_x))
    assert policy.rules == (rule_y, rule_x)


def test_no_sorting_or_hidden_priorities():
    source = (_repo_root() / "leira/flow_policy_projection/flow_policy.py").read_text(encoding="utf-8")
    forbidden = ("sort(", "sorted(", "priority", ".score", "score(", "score=")
    assert all(term not in source.lower() for term in forbidden)


def test_section_order_never_varies():
    projection = evaluate_flow_policy(lifecycle_projection=_lifecycle_projection(), flow_policy=_policy())
    markdown = flow_policy_projection_markdown(projection)
    sections = [
        "# Flow Policy Projection",
        "## Subject",
        "## Policy",
        "## Matched Rule",
        "## Recommended Action",
        "## Evaluation Trace",
        "## Provenance Notice",
    ]
    positions = [markdown.index(section) for section in sections]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/flow_policy_projection/flow_policy.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_repository_scanning_or_filesystem_inspection():
    source = (_repo_root() / "leira/flow_policy_projection/flow_policy.py").read_text(encoding="utf-8")
    forbidden = ("glob", "os.walk", "os.listdir", "scandir", "iterdir", "Path.cwd")
    assert all(term not in source for term in forbidden)


def test_no_ledger_access(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        flow_policy_projection_markdown(
            evaluate_flow_policy(lifecycle_projection=_lifecycle_projection(), flow_policy=_policy())
        )
        assert _event_rows(ledger) == before
    finally:
        ledger.close()
    source = (_repo_root() / "leira/flow_policy_projection/flow_policy.py").read_text(encoding="utf-8")
    assert "LedgerKernel" not in source
    assert "ledger.connection" not in source
    assert "ledger_events" not in source


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        projection = evaluate_flow_policy(lifecycle_projection=_lifecycle_projection(), flow_policy=_policy())
        flow_policy_projection_markdown(projection)
        write_flow_policy_projection(projection, tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_planner_dispatcher_workflow_or_recommendation_heuristics():
    source = (_repo_root() / "leira/flow_policy_projection/flow_policy.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source
    forbidden = ("planner", "workflow", "heuristic", "recommendation_engine")
    assert all(term not in source.lower() for term in forbidden)


def test_no_ai_calls_or_browser_automation():
    source = (_repo_root() / "leira/flow_policy_projection/flow_policy.py").read_text(encoding="utf-8")
    forbidden = ("openai", "anthropic", "browser", "requests")
    assert all(term not in source.lower() for term in forbidden)


def test_no_subprocess_or_shell_commands():
    source = (_repo_root() / "leira/flow_policy_projection/flow_policy.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/flow_policy_projection/dispatcher.py",
        root / "leira/flow_policy_projection/planner.py",
        root / "leira/flow_policy_projection/workflow.py",
        root / "leira/flow_policy_projection/approval.py",
        root / "leira/flow_policy_projection/rejection.py",
        root / "leira/flow_policy_projection/cli.py",
        root / "leira/flow_policy_projection/openai.py",
        root / "leira/flow_policy_projection/claude.py",
        root / "leira/flow_policy_projection/browser.py",
        root / "leira/flow_policy_projection/database.py",
        root / "leira/flow_policy_projection/scanner.py",
        root / "leira/flow_policy_projection/loader.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_does_not_inspect_evidence_or_reconstruct_lifecycle():
    source = (_repo_root() / "leira/flow_policy_projection/flow_policy.py").read_text(encoding="utf-8")
    assert "build_lifecycle_projection" not in source
    assert "EvidencePresence" not in source


def test_provenance_notice_present():
    projection = evaluate_flow_policy(lifecycle_projection=_lifecycle_projection(), flow_policy=_policy())
    markdown = flow_policy_projection_markdown(projection)
    assert PROVENANCE_NOTICE in markdown
