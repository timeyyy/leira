"""Leira v2.0 lifecycle projection: a read model over existing evidence, not a decision.

The projection derives lifecycle state solely from the evidence objects the
caller supplies. It never scans the repository, queries the ledger, or
infers evidence that was not explicitly handed to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROVENANCE_NOTICE = (
    "This projection derives lifecycle state from supplied evidence.\n"
    "It performs no planning, dispatch, execution, approval or workflow transitions."
)

EVIDENCE_LABELS: tuple[str, ...] = (
    "Prompt Draft",
    "Knowledge Gap",
    "Review Question",
    "Review Record",
    "Prompt Revision",
    "Human Decision",
    "Dispatch Record",
    "Implementation Report",
    "Verification Record",
)

_EVIDENCE_LABEL_WIDTH = max(len(label) for label in EVIDENCE_LABELS) + 8


@dataclass(frozen=True)
class EvidencePresence:
    label: str
    present: bool


@dataclass(frozen=True)
class LifecycleProjection:
    subject_id: str
    subject_kind: str
    prompt_draft_present: bool
    knowledge_gap_present: bool
    review_question_present: bool
    review_record_present: bool
    prompt_revision_present: bool
    human_decision_present: bool
    dispatch_record_present: bool
    implementation_report_present: bool
    verification_record_present: bool
    missing_evidence: tuple[str, ...]
    completed_evidence: tuple[str, ...]


def build_lifecycle_projection(
    *,
    subject_id: str,
    subject_kind: str,
    prompt_draft: Any | None = None,
    knowledge_gap: Any | None = None,
    review_question: Any | None = None,
    review_record: Any | None = None,
    prompt_revision: Any | None = None,
    human_decision: Any | None = None,
    dispatch_record: Any | None = None,
    implementation_report: Any | None = None,
    verification_record: Any | None = None,
) -> LifecycleProjection:
    """Derive a lifecycle projection purely from caller-supplied evidence objects.

    Presence means the corresponding argument is not None. Nothing is
    discovered, scanned, queried or inferred beyond that explicit check.
    """

    presences = (
        prompt_draft is not None,
        knowledge_gap is not None,
        review_question is not None,
        review_record is not None,
        prompt_revision is not None,
        human_decision is not None,
        dispatch_record is not None,
        implementation_report is not None,
        verification_record is not None,
    )

    completed_evidence = tuple(
        label for label, present in zip(EVIDENCE_LABELS, presences) if present
    )
    missing_evidence = tuple(
        label for label, present in zip(EVIDENCE_LABELS, presences) if not present
    )

    return LifecycleProjection(
        subject_id=subject_id,
        subject_kind=subject_kind,
        prompt_draft_present=presences[0],
        knowledge_gap_present=presences[1],
        review_question_present=presences[2],
        review_record_present=presences[3],
        prompt_revision_present=presences[4],
        human_decision_present=presences[5],
        dispatch_record_present=presences[6],
        implementation_report_present=presences[7],
        verification_record_present=presences[8],
        missing_evidence=missing_evidence,
        completed_evidence=completed_evidence,
    )


def evidence_presence_table(projection: LifecycleProjection) -> tuple[EvidencePresence, ...]:
    """Reconstruct the fixed-order evidence presence table from a projection's flags."""

    presences = (
        projection.prompt_draft_present,
        projection.knowledge_gap_present,
        projection.review_question_present,
        projection.review_record_present,
        projection.prompt_revision_present,
        projection.human_decision_present,
        projection.dispatch_record_present,
        projection.implementation_report_present,
        projection.verification_record_present,
    )
    return tuple(
        EvidencePresence(label=label, present=present)
        for label, present in zip(EVIDENCE_LABELS, presences)
    )


def _evidence_line(entry: EvidencePresence) -> str:
    status = "PRESENT" if entry.present else "MISSING"
    dots = "." * max(3, _EVIDENCE_LABEL_WIDTH - len(entry.label))
    return f"{entry.label} {dots} {status}"


def lifecycle_projection_markdown(projection: LifecycleProjection) -> str:
    """Render one lifecycle projection as deterministic markdown."""

    lines = [
        "# Lifecycle Projection",
        "",
        "## Subject",
        "",
        f"Subject ID: {projection.subject_id}",
        f"Subject Kind: {projection.subject_kind}",
        "",
        "## Evidence",
        "",
    ]
    for entry in evidence_presence_table(projection):
        lines.append(_evidence_line(entry))
        lines.append("")
    lines.pop()
    lines.extend(["", "## Completed Evidence", ""])
    lines.extend(f"* {label}" for label in projection.completed_evidence)
    lines.extend(["", "## Missing Evidence", ""])
    lines.extend(f"* {label}" for label in projection.missing_evidence)
    lines.extend(
        [
            "",
            "## Provenance Notice",
            "",
            PROVENANCE_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_lifecycle_projection(projection: LifecycleProjection, repo_root: str | Path = ".") -> str:
    """Write deterministic derived lifecycle-projection markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "lifecycle" / f"{projection.subject_id}.lifecycle.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(lifecycle_projection_markdown(projection), encoding="utf-8")
    return output.relative_to(root).as_posix()
