"""Leira v1.14 prompt revisions: evolution evidence, not approval."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROVENANCE_NOTICE = (
    "This record preserves a revised prompt as evidence.\n"
    "It performs no approval, reconciliation, dispatch, planning or execution."
)


@dataclass(frozen=True)
class PromptRevision:
    revision_id: str
    parent_draft_id: str
    review_record_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    source_label: str
    revised_prompt: str
    revision_summary: str


def create_prompt_revision(
    *,
    revision_id: str,
    parent_draft_id: str,
    review_record_ids: list[str] | tuple[str, ...],
    reason_codes: list[str] | tuple[str, ...],
    source_label: str,
    revised_prompt: str,
    revision_summary: str,
) -> PromptRevision:
    """Create one immutable prompt revision record from caller-supplied evidence."""

    return PromptRevision(
        revision_id=revision_id,
        parent_draft_id=parent_draft_id,
        review_record_ids=tuple(review_record_ids),
        reason_codes=tuple(reason_codes),
        source_label=source_label,
        revised_prompt=revised_prompt,
        revision_summary=revision_summary,
    )


def prompt_revision_markdown(revision: PromptRevision) -> str:
    """Render one prompt revision record as deterministic markdown."""

    lines = [
        "# Prompt Revision Record",
        "",
        "## Revision ID",
        "",
        revision.revision_id,
        "",
        "## Parent Draft",
        "",
        revision.parent_draft_id,
        "",
        "## Review Records",
        "",
    ]
    lines.extend(f"* {review_record_id}" for review_record_id in revision.review_record_ids)
    lines.extend(["", "## Source", "", revision.source_label, "", "## Reason Codes", ""])
    lines.extend(f"* {reason_code}" for reason_code in revision.reason_codes)
    lines.extend(
        [
            "",
            "## Revision Summary",
            "",
            revision.revision_summary,
            "",
            "## Revised Prompt",
            "",
            "```text",
            revision.revised_prompt,
            "```",
            "",
            "## Provenance Notice",
            "",
            PROVENANCE_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_prompt_revision(revision: PromptRevision, repo_root: str | Path = ".") -> str:
    """Write deterministic derived prompt-revision markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "prompt_revisions" / f"{revision.revision_id}.revision.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(prompt_revision_markdown(revision), encoding="utf-8")
    return output.relative_to(root).as_posix()
