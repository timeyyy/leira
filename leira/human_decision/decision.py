"""Leira v1.15 human decision records: judgment as evidence, not execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROVENANCE_NOTICE = (
    "This record preserves a human decision as evidence.\n"
    "It performs no dispatch, execution, approval logic, workflow transitions or planning."
)


@dataclass(frozen=True)
class HumanDecision:
    decision_id: str
    subject_id: str
    subject_kind: str
    decision_type: str
    reason_codes: tuple[str, ...]
    source_label: str
    review_record_ids: tuple[str, ...]
    prompt_revision_id: str
    decision_summary: str


def create_human_decision(
    *,
    decision_id: str,
    subject_id: str,
    subject_kind: str,
    decision_type: str,
    reason_codes: list[str] | tuple[str, ...],
    source_label: str,
    review_record_ids: list[str] | tuple[str, ...],
    prompt_revision_id: str,
    decision_summary: str,
) -> HumanDecision:
    """Create one immutable human decision record from caller-supplied evidence."""

    return HumanDecision(
        decision_id=decision_id,
        subject_id=subject_id,
        subject_kind=subject_kind,
        decision_type=decision_type,
        reason_codes=tuple(reason_codes),
        source_label=source_label,
        review_record_ids=tuple(review_record_ids),
        prompt_revision_id=prompt_revision_id,
        decision_summary=decision_summary,
    )


def human_decision_markdown(decision: HumanDecision) -> str:
    """Render one human decision record as deterministic markdown."""

    lines = [
        "# Human Decision Record",
        "",
        "## Decision ID",
        "",
        decision.decision_id,
        "",
        "## Subject",
        "",
        decision.subject_id,
        "",
        "## Subject Kind",
        "",
        decision.subject_kind,
        "",
        "## Decision Type",
        "",
        decision.decision_type,
        "",
        "## Prompt Revision",
        "",
        decision.prompt_revision_id,
        "",
        "## Review Records",
        "",
    ]
    lines.extend(f"* {review_record_id}" for review_record_id in decision.review_record_ids)
    lines.extend(["", "## Source", "", decision.source_label, "", "## Reason Codes", ""])
    lines.extend(f"* {reason_code}" for reason_code in decision.reason_codes)
    lines.extend(
        [
            "",
            "## Decision Summary",
            "",
            decision.decision_summary,
            "",
            "## Provenance Notice",
            "",
            PROVENANCE_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_human_decision(decision: HumanDecision, repo_root: str | Path = ".") -> str:
    """Write deterministic derived human-decision markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "human_decisions" / f"{decision.decision_id}.decision.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(human_decision_markdown(decision), encoding="utf-8")
    return output.relative_to(root).as_posix()
