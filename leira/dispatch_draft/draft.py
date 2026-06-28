"""Leira v3.3 dispatch draft: a reviewable draft, not a Dispatch Record.

This module converts an already-committed HumanDecisionCommit into a
deterministic DispatchDraft by extracting fields directly from the
committed HumanDecision. It never creates a DispatchRecord and never
infers facts beyond what the commit already contains.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from leira.human_decision_commit.commit import HumanDecisionCommit

PROVENANCE_NOTICE = (
    "This draft is a deterministic projection.\n"
    "It does not create a Dispatch Record.\n"
    "It performs no planning, execution, approval or dispatch."
)

# The committed HumanDecision carries no notion of where a dispatch should
# go or how it should run -- those are not yet known when this draft is
# built. A future, explicitly human-invoked commit step (mirroring
# commit_human_decision) supplies them, just as commit_human_decision
# itself required caller-supplied fields the draft did not have.
PENDING_TARGET_LABEL = "PENDING_HUMAN_INPUT"
PENDING_EXECUTION_MODE = "PENDING_HUMAN_INPUT"


@dataclass(frozen=True)
class DispatchDraft:
    subject_id: str
    subject_kind: str
    dispatch_type: str
    target_label: str
    execution_mode: str
    reason_codes: tuple[str, ...]
    draft_summary: str


def build_dispatch_draft(commit: HumanDecisionCommit) -> DispatchDraft:
    """Derive a dispatch draft purely from a supplied HumanDecisionCommit.

    `subject_id`, `subject_kind`, `dispatch_type`, and `reason_codes` are
    copied directly from the committed HumanDecision. `target_label` and
    `execution_mode` are not present on a HumanDecision, so they are left
    as the fixed PENDING_HUMAN_INPUT placeholder rather than inferred.
    `draft_summary` is a fixed template filled in from those same
    extracted fields -- nothing is invented beyond formatting.
    """

    human_decision = commit.human_decision

    draft_summary = (
        f"Dispatch draft for subject '{human_decision.subject_id}' "
        f"({human_decision.subject_kind}): dispatch_type='{human_decision.decision_type}'."
    )

    return DispatchDraft(
        subject_id=human_decision.subject_id,
        subject_kind=human_decision.subject_kind,
        dispatch_type=human_decision.decision_type,
        target_label=PENDING_TARGET_LABEL,
        execution_mode=PENDING_EXECUTION_MODE,
        reason_codes=human_decision.reason_codes,
        draft_summary=draft_summary,
    )


def dispatch_draft_markdown(draft: DispatchDraft) -> str:
    """Render one dispatch draft as deterministic markdown."""

    lines = [
        "# Dispatch Draft",
        "",
        "## Subject",
        "",
        "Subject ID:",
        draft.subject_id,
        "",
        "Subject Kind:",
        draft.subject_kind,
        "",
        "## Dispatch Type",
        "",
        draft.dispatch_type,
        "",
        "## Target",
        "",
        draft.target_label,
        "",
        "## Execution Mode",
        "",
        draft.execution_mode,
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


def write_dispatch_draft(draft: DispatchDraft, repo_root: str | Path = ".") -> str:
    """Write deterministic derived dispatch-draft markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "dispatch_drafts" / f"{draft.subject_id}.draft.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dispatch_draft_markdown(draft), encoding="utf-8")
    return output.relative_to(root).as_posix()
