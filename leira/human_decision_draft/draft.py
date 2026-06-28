"""Leira v3.1 human decision draft: a reviewable draft, not a decision.

This module converts an already-built EngineeringKernelResult into a
deterministic HumanDecisionDraft by extracting existing fields. It never
creates a Human Decision Record, never approves anything, and never
infers facts beyond what the kernel result already contains.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from leira.engineering_kernel.kernel import EngineeringKernelResult

PROVENANCE_NOTICE = (
    "This draft is a deterministic projection.\n"
    "It does not create a Human Decision.\n"
    "It performs no approval, planning, execution or dispatch."
)


@dataclass(frozen=True)
class HumanDecisionDraft:
    subject_id: str
    subject_kind: str
    recommended_action: str
    matched_rule: str | None
    reason_codes: tuple[str, ...]
    draft_summary: str


def build_human_decision_draft(result: EngineeringKernelResult) -> HumanDecisionDraft:
    """Derive a human decision draft purely from a supplied EngineeringKernelResult.

    `recommended_action` and `matched_rule` are copied directly from the
    result's FlowPolicyProjection. `reason_codes` is the FlowPolicyProjection's
    own evaluation trace, preserved exactly. `draft_summary` is a fixed
    template filled in from those same extracted fields -- nothing is
    invented beyond formatting.
    """

    flow_policy_projection = result.flow_policy_projection
    matched_rule = flow_policy_projection.matched_rule_id
    matched_rule_text = matched_rule if matched_rule is not None else "None"

    draft_summary = (
        f"Engineering Kernel recommends '{flow_policy_projection.recommended_action}' "
        f"for subject '{result.subject_id}' ({result.subject_kind}), "
        f"matched rule '{matched_rule_text}'."
    )

    return HumanDecisionDraft(
        subject_id=result.subject_id,
        subject_kind=result.subject_kind,
        recommended_action=flow_policy_projection.recommended_action,
        matched_rule=matched_rule,
        reason_codes=flow_policy_projection.evaluation_trace,
        draft_summary=draft_summary,
    )


def human_decision_draft_markdown(draft: HumanDecisionDraft) -> str:
    """Render one human decision draft as deterministic markdown."""

    lines = [
        "# Human Decision Draft",
        "",
        "## Subject",
        "",
        "Subject ID:",
        draft.subject_id,
        "",
        "Subject Kind:",
        draft.subject_kind,
        "",
        "## Recommended Action",
        "",
        draft.recommended_action,
        "",
        "## Matched Rule",
        "",
        draft.matched_rule if draft.matched_rule is not None else "None",
        "",
        "## Reason Codes",
        "",
    ]
    lines.extend(f"* {entry}" for entry in draft.reason_codes)
    lines.extend(
        [
            "",
            "## Draft Summary",
            "",
            draft.draft_summary,
            "",
            "## Provenance Notice",
            "",
            PROVENANCE_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_human_decision_draft(draft: HumanDecisionDraft, repo_root: str | Path = ".") -> str:
    """Write deterministic derived human-decision-draft markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "human_decision_drafts" / f"{draft.subject_id}.draft.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(human_decision_draft_markdown(draft), encoding="utf-8")
    return output.relative_to(root).as_posix()
