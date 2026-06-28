"""Leira v3.2 human decision commit: explicit human conversion of a draft into a record.

This module converts an already-built HumanDecisionDraft into a real
HumanDecisionRecord, but only when a human explicitly calls
`commit_human_decision`. It calls the existing `create_human_decision`
exactly once with fields extracted directly from the draft and the
caller-supplied commit details -- nothing is inferred or invented beyond
that direct extraction, and no field is altered along the way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from leira.human_decision.decision import (
    HumanDecision,
    create_human_decision,
    human_decision_markdown,
)
from leira.human_decision_draft.draft import HumanDecisionDraft

PROVENANCE_NOTICE = (
    "This commit records that a human explicitly chose to convert a Human Decision Draft "
    "into a Human Decision Record.\n"
    "No planning, approval logic, dispatch or execution occurs here."
)


@dataclass(frozen=True)
class HumanDecisionCommit:
    commit_id: str
    draft_id: str
    human_decision: HumanDecision


def commit_human_decision(
    draft: HumanDecisionDraft,
    *,
    decision_id: str,
    source_label: str,
    decision_summary: str,
    review_record_ids: list[str] | tuple[str, ...],
    prompt_revision_id: str,
) -> HumanDecisionCommit:
    """Convert an explicitly approved HumanDecisionDraft into a HumanDecisionRecord.

    This function exists only to be called when a human has explicitly
    decided to commit a draft. It calls `create_human_decision` exactly
    once, passing through the draft's own fields and the caller-supplied
    commit details unmodified.
    """

    human_decision = create_human_decision(
        decision_id=decision_id,
        subject_id=draft.subject_id,
        subject_kind=draft.subject_kind,
        decision_type=draft.recommended_action,
        reason_codes=draft.reason_codes,
        source_label=source_label,
        review_record_ids=review_record_ids,
        prompt_revision_id=prompt_revision_id,
        decision_summary=decision_summary,
    )

    return HumanDecisionCommit(
        commit_id=decision_id,
        draft_id=draft.subject_id,
        human_decision=human_decision,
    )


def human_decision_commit_markdown(commit: HumanDecisionCommit) -> str:
    """Render one human decision commit as deterministic markdown."""

    lines = [
        "# Human Decision Commit",
        "",
        "## Commit ID",
        "",
        commit.commit_id,
        "",
        "## Draft",
        "",
        commit.draft_id,
        "",
        "## Human Decision",
        "",
        "```text",
        human_decision_markdown(commit.human_decision),
        "```",
        "",
        "## Provenance Notice",
        "",
        PROVENANCE_NOTICE,
        "",
    ]
    return "\n".join(lines)


def write_human_decision_commit(commit: HumanDecisionCommit, repo_root: str | Path = ".") -> str:
    """Write deterministic derived human-decision-commit markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "human_decision_commits" / f"{commit.commit_id}.commit.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(human_decision_commit_markdown(commit), encoding="utf-8")
    return output.relative_to(root).as_posix()
