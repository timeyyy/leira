"""Leira v2.1 flow policy projection: deterministic rule evaluation, not a decision.

This module evaluates an explicit FlowPolicy against an explicit
LifecycleProjection. It never inspects evidence directly, never reconstructs
lifecycle state, and never loads policy from disk -- everything required for
evaluation is supplied by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from leira.lifecycle_projection.lifecycle import LifecycleProjection

PROVENANCE_NOTICE = (
    "This projection evaluates explicit deterministic policy rules.\n"
    "It performs no planning, approval, dispatch, execution or autonomous decision making."
)

NO_MATCH = "NO_MATCH"


@dataclass(frozen=True)
class FlowRule:
    rule_id: str
    required_completed: tuple[str, ...]
    required_missing: tuple[str, ...]
    result_action: str


@dataclass(frozen=True)
class FlowPolicy:
    policy_name: str
    rules: tuple[FlowRule, ...]


@dataclass(frozen=True)
class FlowPolicyProjection:
    subject_id: str
    policy_name: str
    matched_rule_id: str | None
    recommended_action: str
    evaluation_trace: tuple[str, ...]


def create_flow_rule(
    *,
    rule_id: str,
    required_completed: list[str] | tuple[str, ...],
    required_missing: list[str] | tuple[str, ...],
    result_action: str,
) -> FlowRule:
    """Create one immutable flow rule from caller-supplied requirements."""

    return FlowRule(
        rule_id=rule_id,
        required_completed=tuple(required_completed),
        required_missing=tuple(required_missing),
        result_action=result_action,
    )


def create_flow_policy(
    *,
    policy_name: str,
    rules: list[FlowRule] | tuple[FlowRule, ...],
) -> FlowPolicy:
    """Create one immutable flow policy from a caller-supplied ordered rule tuple."""

    return FlowPolicy(policy_name=policy_name, rules=tuple(rules))


def _rule_matches(rule: FlowRule, lifecycle_projection: LifecycleProjection) -> bool:
    return all(
        label in lifecycle_projection.completed_evidence for label in rule.required_completed
    ) and all(label in lifecycle_projection.missing_evidence for label in rule.required_missing)


def evaluate_flow_policy(
    *,
    lifecycle_projection: LifecycleProjection,
    flow_policy: FlowPolicy,
) -> FlowPolicyProjection:
    """Evaluate a flow policy's rules, strictly in caller order, against a lifecycle projection.

    The first rule whose requirements are satisfied is selected. No rule is
    ranked, scored, or evaluated out of order, and evaluation stops as soon
    as a match is found.
    """

    trace: list[str] = []
    matched_rule_id: str | None = None
    recommended_action = NO_MATCH

    for rule in flow_policy.rules:
        if _rule_matches(rule, lifecycle_projection):
            trace.append(f"{rule.rule_id}: MATCHED")
            matched_rule_id = rule.rule_id
            recommended_action = rule.result_action
            break
        trace.append(f"{rule.rule_id}: SKIPPED")

    return FlowPolicyProjection(
        subject_id=lifecycle_projection.subject_id,
        policy_name=flow_policy.policy_name,
        matched_rule_id=matched_rule_id,
        recommended_action=recommended_action,
        evaluation_trace=tuple(trace),
    )


def flow_policy_projection_markdown(projection: FlowPolicyProjection) -> str:
    """Render one flow policy projection as deterministic markdown."""

    lines = [
        "# Flow Policy Projection",
        "",
        "## Subject",
        "",
        projection.subject_id,
        "",
        "## Policy",
        "",
        projection.policy_name,
        "",
        "## Matched Rule",
        "",
        projection.matched_rule_id if projection.matched_rule_id is not None else "None",
        "",
        "## Recommended Action",
        "",
        projection.recommended_action,
        "",
        "## Evaluation Trace",
        "",
    ]
    lines.extend(f"* {entry}" for entry in projection.evaluation_trace)
    lines.extend(
        [
            "",
            "## Provenance Notice",
            "",
            PROVENANCE_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_flow_policy_projection(projection: FlowPolicyProjection, repo_root: str | Path = ".") -> str:
    """Write deterministic derived flow-policy-projection markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "flow_policy" / f"{projection.subject_id}.flow.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(flow_policy_projection_markdown(projection), encoding="utf-8")
    return output.relative_to(root).as_posix()
