"""Leira v3.0 engineering kernel: a deterministic pipeline over existing projection modules.

This module owns no engineering knowledge of its own. It coordinates the
existing Lifecycle Projection, Missing Evidence Projection, Flow Policy
Projection, and Engineering State Projection modules, in that fixed order,
from caller-supplied evidence and a caller-supplied FlowPolicy. It performs
no judgement, no mutation, no repository scanning, and no policy loading.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from leira.engineering_state_projection.engineering_state import (
    EngineeringStateProjection,
    build_engineering_state_projection,
    engineering_state_projection_markdown,
)
from leira.flow_policy_projection.flow_policy import (
    FlowPolicy,
    FlowPolicyProjection,
    evaluate_flow_policy,
    flow_policy_projection_markdown,
)
from leira.lifecycle_projection.lifecycle import (
    LifecycleProjection,
    build_lifecycle_projection,
    lifecycle_projection_markdown,
)
from leira.missing_evidence_projection.missing_evidence import (
    MissingEvidenceProjection,
    build_missing_evidence_projection,
    missing_evidence_projection_markdown,
)

PROVENANCE_NOTICE = (
    "This result composes deterministic kernel stages.\n"
    "It performs no planning, approval, execution or dispatch."
)


@dataclass(frozen=True)
class EngineeringKernelResult:
    subject_id: str
    subject_kind: str
    lifecycle_projection: LifecycleProjection
    missing_evidence_projection: MissingEvidenceProjection
    flow_policy_projection: FlowPolicyProjection
    engineering_state_projection: EngineeringStateProjection


def run_engineering_kernel(
    *,
    subject_id: str,
    subject_kind: str,
    flow_policy: FlowPolicy,
    prompt_draft: Any | None = None,
    knowledge_gap: Any | None = None,
    review_question: Any | None = None,
    review_record: Any | None = None,
    prompt_revision: Any | None = None,
    human_decision: Any | None = None,
    dispatch_record: Any | None = None,
    implementation_report: Any | None = None,
    verification_record: Any | None = None,
) -> EngineeringKernelResult:
    """Run the fixed four-stage deterministic pipeline over caller-supplied inputs.

    1. Build the Lifecycle Projection from the supplied evidence.
    2. Build the Missing Evidence Projection from that Lifecycle Projection.
    3. Evaluate the supplied FlowPolicy against that Lifecycle Projection.
    4. Build the Engineering State Projection from the three results above.

    Each stage runs exactly once, in this order. Nothing is scanned,
    loaded, or inferred beyond what the caller supplied.
    """

    lifecycle_projection = build_lifecycle_projection(
        subject_id=subject_id,
        subject_kind=subject_kind,
        prompt_draft=prompt_draft,
        knowledge_gap=knowledge_gap,
        review_question=review_question,
        review_record=review_record,
        prompt_revision=prompt_revision,
        human_decision=human_decision,
        dispatch_record=dispatch_record,
        implementation_report=implementation_report,
        verification_record=verification_record,
    )

    missing_evidence_projection = build_missing_evidence_projection(lifecycle_projection)

    flow_policy_projection = evaluate_flow_policy(
        lifecycle_projection=lifecycle_projection, flow_policy=flow_policy
    )

    engineering_state_projection = build_engineering_state_projection(
        lifecycle_projection, missing_evidence_projection, flow_policy_projection
    )

    return EngineeringKernelResult(
        subject_id=subject_id,
        subject_kind=subject_kind,
        lifecycle_projection=lifecycle_projection,
        missing_evidence_projection=missing_evidence_projection,
        flow_policy_projection=flow_policy_projection,
        engineering_state_projection=engineering_state_projection,
    )


def engineering_kernel_markdown(result: EngineeringKernelResult) -> str:
    """Render one engineering kernel result as deterministic markdown."""

    lines = [
        "# Engineering Kernel",
        "",
        "## Subject",
        "",
        "Subject ID:",
        result.subject_id,
        "",
        "Subject Kind:",
        result.subject_kind,
        "",
        "## Lifecycle",
        "",
        "```text",
        lifecycle_projection_markdown(result.lifecycle_projection),
        "```",
        "",
        "## Missing Evidence",
        "",
        "```text",
        missing_evidence_projection_markdown(result.missing_evidence_projection),
        "```",
        "",
        "## Flow Policy",
        "",
        "```text",
        flow_policy_projection_markdown(result.flow_policy_projection),
        "```",
        "",
        "## Engineering State",
        "",
        "```text",
        engineering_state_projection_markdown(result.engineering_state_projection),
        "```",
        "",
        "## Provenance Notice",
        "",
        PROVENANCE_NOTICE,
        "",
    ]
    return "\n".join(lines)


def write_engineering_kernel(result: EngineeringKernelResult, repo_root: str | Path = ".") -> str:
    """Write deterministic derived engineering-kernel markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "engineering_kernel" / f"{result.subject_id}.kernel.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(engineering_kernel_markdown(result), encoding="utf-8")
    return output.relative_to(root).as_posix()
