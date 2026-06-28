from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.engineering_state_projection.engineering_state import (
    PROVENANCE_NOTICE,
    EngineeringStateProjection,
    EngineeringSummary,
    build_engineering_state_projection,
    engineering_state_projection_markdown,
    write_engineering_state_projection,
)
from leira.flow_policy_projection.flow_policy import create_flow_policy, create_flow_rule, evaluate_flow_policy
from leira.inbox.inbox import InboxKernel
from leira.lifecycle_projection.lifecycle import build_lifecycle_projection
from leira.missing_evidence_projection.missing_evidence import build_missing_evidence_projection
from leira.project_state.state import build_project_state


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _ready_rule():
    return create_flow_rule(
        rule_id="ready_for_decision",
        required_completed=("Prompt Revision",),
        required_missing=("Human Decision",),
        result_action="REQUEST_HUMAN_DECISION",
    )


def _catch_all_rule():
    return create_flow_rule(
        rule_id="catch_all", required_completed=(), required_missing=(), result_action="WAIT"
    )


def _mixed_lifecycle():
    return build_lifecycle_projection(
        subject_id="subject-1",
        subject_kind="prompt_draft",
        prompt_draft="x",
        knowledge_gap="x",
        review_question="x",
        review_record="x",
        prompt_revision="x",
    )


def _all_present_lifecycle():
    return build_lifecycle_projection(
        subject_id="subject-1",
        subject_kind="prompt_draft",
        prompt_draft="x",
        knowledge_gap="x",
        review_question="x",
        review_record="x",
        prompt_revision="x",
        human_decision="x",
        dispatch_record="x",
        implementation_report="x",
        verification_record="x",
    )


def _build(lifecycle_projection, rules):
    missing_evidence_projection = build_missing_evidence_projection(lifecycle_projection)
    flow_policy = create_flow_policy(policy_name="default_policy", rules=rules)
    flow_policy_projection = evaluate_flow_policy(
        lifecycle_projection=lifecycle_projection, flow_policy=flow_policy
    )
    return build_engineering_state_projection(
        lifecycle_projection, missing_evidence_projection, flow_policy_projection
    )


def _mixed():
    return _build(_mixed_lifecycle(), (_ready_rule(), _catch_all_rule()))


def _all_present():
    return _build(_all_present_lifecycle(), (_catch_all_rule(),))


def _no_match_lifecycle():
    return build_lifecycle_projection(
        subject_id="subject-1",
        subject_kind="prompt_draft",
        prompt_draft="x",
        knowledge_gap="x",
    )


def _no_match():
    return _build(_no_match_lifecycle(), (_ready_rule(),))


def test_engineering_summary_immutable():
    summary = EngineeringSummary(
        completed_evidence_count=1, missing_evidence_count=2, recommended_action="WAIT", matched_rule=None
    )
    with pytest.raises(FrozenInstanceError):
        summary.recommended_action = "OTHER"


def test_engineering_state_projection_immutable():
    projection = _mixed()
    with pytest.raises(FrozenInstanceError):
        projection.subject_id = "other"


def test_deterministic_projection_creation():
    lp = _mixed_lifecycle()
    rules = (_ready_rule(), _catch_all_rule())
    first = _build(lp, rules)
    second = _build(lp, rules)
    assert first == second
    assert isinstance(first, EngineeringStateProjection)


def test_deterministic_markdown():
    projection = _mixed()
    first = engineering_state_projection_markdown(projection)
    second = engineering_state_projection_markdown(projection)
    assert first == second
    assert first.startswith("# Engineering State Projection\n")


def test_byte_identical_repeated_rendering():
    projection = _mixed()
    first = engineering_state_projection_markdown(projection).encode("utf-8")
    second = engineering_state_projection_markdown(projection).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    projection = _mixed()
    path = write_engineering_state_projection(projection, tmp_path)
    assert path == ".leira/engineering_state/subject-1.engineering.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == engineering_state_projection_markdown(
        projection
    )


def test_byte_identical_repeated_writes(tmp_path):
    projection = _mixed()
    first_path = write_engineering_state_projection(projection, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_engineering_state_projection(projection, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_lifecycle_projection_preserved_exactly():
    lp = _mixed_lifecycle()
    projection = _build(lp, (_catch_all_rule(),))
    assert projection.lifecycle_projection == lp
    assert projection.lifecycle_projection is lp


def test_missing_evidence_projection_preserved_exactly():
    lp = _mixed_lifecycle()
    mep = build_missing_evidence_projection(lp)
    flow_policy = create_flow_policy(policy_name="p", rules=(_catch_all_rule(),))
    fpp = evaluate_flow_policy(lifecycle_projection=lp, flow_policy=flow_policy)
    projection = build_engineering_state_projection(lp, mep, fpp)
    assert projection.missing_evidence_projection == mep
    assert projection.missing_evidence_projection is mep


def test_flow_policy_projection_preserved_exactly():
    lp = _mixed_lifecycle()
    mep = build_missing_evidence_projection(lp)
    flow_policy = create_flow_policy(policy_name="p", rules=(_catch_all_rule(),))
    fpp = evaluate_flow_policy(lifecycle_projection=lp, flow_policy=flow_policy)
    projection = build_engineering_state_projection(lp, mep, fpp)
    assert projection.flow_policy_projection == fpp
    assert projection.flow_policy_projection is fpp


def test_summary_values_copied_correctly():
    projection = _mixed()
    mep = projection.missing_evidence_projection
    fpp = projection.flow_policy_projection
    assert projection.summary.completed_evidence_count == mep.completed_count
    assert projection.summary.missing_evidence_count == mep.missing_count
    assert projection.summary.recommended_action == fpp.recommended_action
    assert projection.summary.matched_rule == fpp.matched_rule_id


def test_all_present_scenario():
    projection = _all_present()
    assert projection.summary.completed_evidence_count == 9
    assert projection.summary.missing_evidence_count == 0
    assert projection.summary.recommended_action == "WAIT"
    assert projection.summary.matched_rule == "catch_all"
    markdown = engineering_state_projection_markdown(projection)
    assert "WAIT" in markdown


def test_mixed_scenario():
    projection = _mixed()
    assert projection.summary.completed_evidence_count == 5
    assert projection.summary.missing_evidence_count == 4
    assert projection.summary.recommended_action == "REQUEST_HUMAN_DECISION"
    assert projection.summary.matched_rule == "ready_for_decision"


def test_no_match_flow_policy_scenario():
    projection = _no_match()
    assert projection.summary.recommended_action == "NO_MATCH"
    assert projection.summary.matched_rule is None
    markdown = engineering_state_projection_markdown(projection)
    assert "Matched Rule:\nNone" in markdown
    assert "NO_MATCH" in markdown


def test_section_order_never_varies():
    markdown = engineering_state_projection_markdown(_mixed())
    # Nested projection markdown is embedded under the Lifecycle/Missing
    # Evidence/Flow Policy sections and reuses some of the same header text
    # (e.g. the Lifecycle Projection has its own "## Missing Evidence"
    # section), so bare header strings are not unique. These markers
    # include enough trailing context to identify the outer section only.
    markers = [
        "# Engineering State Projection",
        "## Subject\n\nSubject ID:",
        "## Lifecycle\n\n```text",
        "## Missing Evidence\n\n```text",
        "## Flow Policy\n\n```text",
        "## Summary\n\nCompleted Evidence:",
        "## Provenance Notice\n\nThis projection composes",
    ]
    positions = [markdown.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/engineering_state_projection/engineering_state.py").read_text(
        encoding="utf-8"
    )
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_repository_scanning_or_filesystem_inspection():
    source = (_repo_root() / "leira/engineering_state_projection/engineering_state.py").read_text(
        encoding="utf-8"
    )
    forbidden = ("glob", "os.walk", "os.listdir", "scandir", "iterdir", "Path.cwd")
    assert all(term not in source for term in forbidden)


def test_no_ledger_access(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        engineering_state_projection_markdown(_mixed())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()
    source = (_repo_root() / "leira/engineering_state_projection/engineering_state.py").read_text(
        encoding="utf-8"
    )
    assert "LedgerKernel" not in source
    assert "ledger.connection" not in source
    assert "ledger_events" not in source


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        projection = _mixed()
        engineering_state_projection_markdown(projection)
        write_engineering_state_projection(projection, tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_planner_dispatcher_recommendation_engine_or_workflow_execution():
    source = (_repo_root() / "leira/engineering_state_projection/engineering_state.py").read_text(
        encoding="utf-8"
    )
    assert "dispatcher" not in source
    assert "def plan" not in source
    forbidden = ("planner", "recommendation_engine", "workflow_engine", "def execute")
    assert all(term not in source.lower() for term in forbidden)


def test_no_ai_calls_or_browser_automation():
    source = (_repo_root() / "leira/engineering_state_projection/engineering_state.py").read_text(
        encoding="utf-8"
    )
    forbidden = ("openai", "anthropic", "browser", "requests")
    assert all(term not in source.lower() for term in forbidden)


def test_no_subprocess_or_shell_commands():
    source = (_repo_root() / "leira/engineering_state_projection/engineering_state.py").read_text(
        encoding="utf-8"
    )
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(")
    assert all(term not in source for term in forbidden)


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/engineering_state_projection/dispatcher.py",
        root / "leira/engineering_state_projection/planner.py",
        root / "leira/engineering_state_projection/workflow.py",
        root / "leira/engineering_state_projection/approval.py",
        root / "leira/engineering_state_projection/rejection.py",
        root / "leira/engineering_state_projection/cli.py",
        root / "leira/engineering_state_projection/openai.py",
        root / "leira/engineering_state_projection/claude.py",
        root / "leira/engineering_state_projection/browser.py",
        root / "leira/engineering_state_projection/database.py",
        root / "leira/engineering_state_projection/scanner.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_does_not_inspect_evidence_reconstruct_lifecycle_or_evaluate_flow_policy():
    source = (_repo_root() / "leira/engineering_state_projection/engineering_state.py").read_text(
        encoding="utf-8"
    )
    assert "build_lifecycle_projection" not in source
    assert "EvidencePresence" not in source
    assert "evaluate_flow_policy" not in source


def test_provenance_notice_present():
    markdown = engineering_state_projection_markdown(_mixed())
    assert PROVENANCE_NOTICE in markdown
