"""Leira v1.11 prompt feedback: evidence, not reconciliation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


NON_RECONCILIATION_NOTICE = (
    "This feedback bundle preserves observations and disagreements.\n"
    "It does not reconcile, rank, approve, reject, or revise the draft."
)


@dataclass(frozen=True)
class FeedbackRecord:
    draft_id: str
    participant: str
    feedback_text: str
    feedback_hash: str
    source_label: str


@dataclass(frozen=True)
class FeedbackBundle:
    draft_id: str
    feedback_records: tuple[FeedbackRecord, ...]


def record_feedback(
    *,
    draft_id: str,
    participant: str,
    feedback_text: str,
    source_label: str,
) -> FeedbackRecord:
    """Create one deterministic feedback record from explicit evidence."""

    return FeedbackRecord(
        draft_id=draft_id,
        participant=participant,
        feedback_text=feedback_text,
        feedback_hash=_feedback_hash(feedback_text),
        source_label=source_label,
    )


def bundle_feedback(draft_id: str, records: list[FeedbackRecord]) -> FeedbackBundle:
    """Bundle records for one draft, sorted deterministically by participant."""

    filtered = tuple(record for record in records if record.draft_id == draft_id)
    ordered = tuple(
        sorted(
            filtered,
            key=lambda record: (
                record.participant,
                record.source_label,
                record.feedback_hash,
                record.feedback_text,
            ),
        )
    )
    return FeedbackBundle(draft_id=draft_id, feedback_records=ordered)


def feedback_markdown(bundle: FeedbackBundle) -> str:
    """Render a deterministic markdown feedback bundle."""

    participants = sorted({record.participant for record in bundle.feedback_records})
    source_labels = sorted({record.source_label for record in bundle.feedback_records})
    lines = [
        "# Prompt Feedback Bundle",
        "",
        "## Draft ID",
        "",
        bundle.draft_id,
        "",
        "## Participants",
        "",
    ]
    lines.extend(f"* {participant}" for participant in participants)
    lines.extend(["", "## Feedback Records", ""])
    for record in bundle.feedback_records:
        lines.extend(
            [
                f"### {record.participant}",
                "",
                f"source_label: {record.source_label}",
                f"feedback_hash: {record.feedback_hash}",
                "",
                "```text",
                record.feedback_text,
                "```",
                "",
            ]
        )
    lines.extend(["## Source Labels", ""])
    lines.extend(f"* {source_label}" for source_label in source_labels)
    lines.extend(["", "## Explicit Non-Reconciliation Notice", "", NON_RECONCILIATION_NOTICE, ""])
    return "\n".join(lines)


def write_feedback_bundle(
    bundle: FeedbackBundle,
    repo_root: str | Path = ".",
) -> str:
    """Write deterministic derived feedback markdown and return its relative path."""

    root = Path(repo_root)
    output = root / ".leira" / "feedback" / f"{bundle.draft_id}.feedback.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(feedback_markdown(bundle), encoding="utf-8")
    return output.relative_to(root).as_posix()


def _feedback_hash(feedback_text: str) -> str:
    return hashlib.sha256(feedback_text.encode("utf-8")).hexdigest()
