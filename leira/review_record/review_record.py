"""Leira v1.13 review records: evidence, not judgment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROVENANCE_NOTICE = (
    "This record preserves reviewer evidence exactly as supplied.\n"
    "It performs no interpretation, approval, reconciliation or revision."
)


@dataclass(frozen=True)
class ReviewRecord:
    review_id: str
    draft_id: str
    review_question_id: str
    reviewer_label: str
    response_text: str
    source_label: str
    reason_codes: tuple[str, ...]
    attachments: tuple[str, ...]


def create_review_record(
    *,
    review_id: str,
    draft_id: str,
    review_question_id: str,
    reviewer_label: str,
    response_text: str,
    source_label: str,
    reason_codes: list[str] | tuple[str, ...],
    attachments: list[str] | tuple[str, ...],
) -> ReviewRecord:
    """Create one immutable review record from caller-supplied evidence."""

    return ReviewRecord(
        review_id=review_id,
        draft_id=draft_id,
        review_question_id=review_question_id,
        reviewer_label=reviewer_label,
        response_text=response_text,
        source_label=source_label,
        reason_codes=tuple(reason_codes),
        attachments=tuple(attachments),
    )


def review_record_markdown(record: ReviewRecord) -> str:
    """Render one review record as deterministic markdown."""

    lines = [
        "# Review Record",
        "",
        "## Review ID",
        "",
        record.review_id,
        "",
        "## Draft ID",
        "",
        record.draft_id,
        "",
        "## Review Question",
        "",
        record.review_question_id,
        "",
        "## Reviewer",
        "",
        record.reviewer_label,
        "",
        "## Source",
        "",
        record.source_label,
        "",
        "## Reason Codes",
        "",
    ]
    lines.extend(f"* {reason_code}" for reason_code in record.reason_codes)
    lines.extend(["", "## Response", "", "```text", record.response_text, "```", "", "## Attachments", ""])
    lines.extend(f"* {attachment}" for attachment in record.attachments)
    lines.extend(["", "## Provenance Notice", "", PROVENANCE_NOTICE, ""])
    return "\n".join(lines)


def write_review_record(record: ReviewRecord, repo_root: str | Path = ".") -> str:
    """Write deterministic derived review-record markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "review_records" / f"{record.review_id}.review.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(review_record_markdown(record), encoding="utf-8")
    return output.relative_to(root).as_posix()
