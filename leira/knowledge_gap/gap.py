"""Leira v1.12 knowledge gaps: targeted questions, not consensus."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VALID_CATEGORIES = frozenset(
    {
        "ordering",
        "assumption",
        "invariant",
        "authority_boundary",
        "evidence",
        "risk",
        "scope",
        "unknown",
    }
)

DEFAULT_LEIRA_REVIEW_QUESTION = (
    "What is the single highest-leverage question, distinction, or hidden assumption "
    "that, if surfaced now, would most improve the likelihood that this work reaches "
    "its intended destination while remaining faithful to its mission?"
)

DEFAULT_REVIEWER_INSTRUCTIONS = (
    "Prioritize hidden assumptions, category errors, missing invariants, ordering mistakes, "
    "irreversible decisions, simpler formulations, and things that should be deleted rather "
    "than added.\n\n"
    "Do not optimize for novelty.\n\n"
    "Do not search for problems merely to be useful.\n\n"
    "If the current slice is already appropriately scoped, say so explicitly.\n\n"
    "If you disagree with previous reviewers, preserve the disagreement rather than averaging "
    "it away.\n\n"
    "The goal is not consensus.\n\n"
    "The goal is to reveal information that is currently invisible."
)

NON_CONSENSUS_NOTICE = (
    "This review question is intended to reveal missing information.\n"
    "It does not request consensus, voting, approval, or authority transfer."
)


@dataclass(frozen=True)
class KnowledgeGap:
    draft_id: str
    question: str
    category: str
    source_label: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class ReviewQuestion:
    draft_id: str
    knowledge_gap: KnowledgeGap
    question_text: str
    target_reviewers: tuple[str, ...]


def create_knowledge_gap(
    *,
    draft_id: str,
    question: str,
    category: str = "unknown",
    source_label: str,
    reason_codes: list[str] | tuple[str, ...],
) -> KnowledgeGap:
    """Create one immutable knowledge gap from explicit metadata."""

    normalized_category = category if category in VALID_CATEGORIES else "unknown"
    return KnowledgeGap(
        draft_id=draft_id,
        question=question,
        category=normalized_category,
        source_label=source_label,
        reason_codes=tuple(reason_codes),
    )


def create_review_question(
    knowledge_gap: KnowledgeGap,
    *,
    target_reviewers: list[str] | tuple[str, ...],
    question_text: str = DEFAULT_LEIRA_REVIEW_QUESTION,
) -> ReviewQuestion:
    """Create a deterministic review question for an explicit knowledge gap."""

    return ReviewQuestion(
        draft_id=knowledge_gap.draft_id,
        knowledge_gap=knowledge_gap,
        question_text=question_text,
        target_reviewers=tuple(target_reviewers),
    )


def review_question_markdown(
    review_question: ReviewQuestion,
    *,
    reviewer_instructions: str = DEFAULT_REVIEWER_INSTRUCTIONS,
) -> str:
    """Render a deterministic markdown review request."""

    gap = review_question.knowledge_gap
    lines = [
        "# Knowledge Gap Review Question",
        "",
        "## Draft ID",
        "",
        review_question.draft_id,
        "",
        "## Category",
        "",
        gap.category,
        "",
        "## Reason Codes",
        "",
    ]
    lines.extend(f"* {reason_code}" for reason_code in gap.reason_codes)
    lines.extend(["", "## Target Reviewers", ""])
    lines.extend(f"* {reviewer}" for reviewer in review_question.target_reviewers)
    lines.extend(
        [
            "",
            "## Review Question",
            "",
            review_question.question_text,
            "",
            "## Reviewer Instructions",
            "",
            reviewer_instructions,
            "",
            "## Non-Consensus Notice",
            "",
            NON_CONSENSUS_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_review_question(
    review_question: ReviewQuestion,
    repo_root: str | Path = ".",
) -> str:
    """Write deterministic derived review-question markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "review_questions" / f"{review_question.draft_id}.review_question.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(review_question_markdown(review_question), encoding="utf-8")
    return output.relative_to(root).as_posix()
