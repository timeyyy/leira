"""Leira v2.3 engineering state projection: composition of existing projections, not a decision.

This module composes an already-built LifecycleProjection,
MissingEvidenceProjection, and FlowPolicyProjection into a single
engineering snapshot. It never inspects evidence directly, never
reconstructs lifecycle state, never evaluates flow policy, and never loads
anything from disk -- everything required for composition is supplied by
the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from leira.flow_policy_projection.flow_policy import (
    FlowPolicyProjection,
    flow_policy_projection_markdown,
)
from leira.lifecycle_projection.lifecycle import (
    LifecycleProjection,
    lifecycle_projection_markdown,
)
from leira.missing_evidence_projection.missing_evidence import (
    MissingEvidenceProjection,
    missing_evidence_projection_markdown,
)

PROVENANCE_NOTICE = (
    "This projection composes deterministic projections into a single engineering snapshot.\n"
    "It performs no planning, recommendation, approval, dispatch, execution or workflow transitions."
)


@dataclass(frozen=True)
class EngineeringSummary:
    completed_evidence_count: int
    missing_evidence_count: int
    recommended_action: str
    matched_rule: str | None


@dataclass(frozen=True)
class EngineeringStateProjection:
    subject_id: str
    subject_kind: str
    lifecycle_projection: LifecycleProjection
    missing_evidence_projection: MissingEvidenceProjection
    flow_policy_projection: FlowPolicyProjection
    summary: EngineeringSummary


def build_engineering_state_projection(
    lifecycle_projection: LifecycleProjection,
    missing_evidence_projection: MissingEvidenceProjection,
    flow_policy_projection: FlowPolicyProjection,
) -> EngineeringStateProjection:
    """Compose three already-built projections into one engineering snapshot.

    Every value is copied directly from the supplied projections. Nothing
    is inferred, recomputed, or reconstructed.
    """

    summary = EngineeringSummary(
        completed_evidence_count=missing_evidence_projection.completed_count,
        missing_evidence_count=missing_evidence_projection.missing_count,
        recommended_action=flow_policy_projection.recommended_action,
        matched_rule=flow_policy_projection.matched_rule_id,
    )

    return EngineeringStateProjection(
        subject_id=lifecycle_projection.subject_id,
        subject_kind=lifecycle_projection.subject_kind,
        lifecycle_projection=lifecycle_projection,
        missing_evidence_projection=missing_evidence_projection,
        flow_policy_projection=flow_policy_projection,
        summary=summary,
    )


def engineering_state_projection_markdown(projection: EngineeringStateProjection) -> str:
    """Render one engineering state projection as deterministic markdown."""

    summary = projection.summary
    matched_rule = summary.matched_rule if summary.matched_rule is not None else "None"

    lines = [
        "# Engineering State Projection",
        "",
        "## Subject",
        "",
        "Subject ID:",
        projection.subject_id,
        "",
        "Subject Kind:",
        projection.subject_kind,
        "",
        "## Lifecycle",
        "",
        "```text",
        lifecycle_projection_markdown(projection.lifecycle_projection),
        "```",
        "",
        "## Missing Evidence",
        "",
        "```text",
        missing_evidence_projection_markdown(projection.missing_evidence_projection),
        "```",
        "",
        "## Flow Policy",
        "",
        "```text",
        flow_policy_projection_markdown(projection.flow_policy_projection),
        "```",
        "",
        "## Summary",
        "",
        "Completed Evidence:",
        str(summary.completed_evidence_count),
        "",
        "Missing Evidence:",
        str(summary.missing_evidence_count),
        "",
        "Recommended Action:",
        summary.recommended_action,
        "",
        "Matched Rule:",
        matched_rule,
        "",
        "## Provenance Notice",
        "",
        PROVENANCE_NOTICE,
        "",
    ]
    return "\n".join(lines)


def write_engineering_state_projection(
    projection: EngineeringStateProjection, repo_root: str | Path = "."
) -> str:
    """Write deterministic derived engineering-state-projection markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "engineering_state" / f"{projection.subject_id}.engineering.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(engineering_state_projection_markdown(projection), encoding="utf-8")
    return output.relative_to(root).as_posix()
