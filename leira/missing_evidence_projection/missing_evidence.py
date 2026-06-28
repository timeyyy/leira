"""Leira v2.2 missing evidence projection: a read model over a Lifecycle Projection, not a decision.

This module derives which lifecycle evidence is absent solely from a
caller-supplied LifecycleProjection. It never inspects evidence directly,
never reconstructs lifecycle state, and never scans the repository or
filesystem -- everything required for derivation is supplied by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from leira.lifecycle_projection.lifecycle import LifecycleProjection

PROVENANCE_NOTICE = (
    "This projection derives missing lifecycle evidence from a supplied Lifecycle Projection.\n"
    "It performs no planning, recommendation, dispatch, execution, approval or workflow transitions."
)


@dataclass(frozen=True)
class MissingEvidence:
    label: str


@dataclass(frozen=True)
class MissingEvidenceProjection:
    subject_id: str
    subject_kind: str
    missing_evidence: tuple[MissingEvidence, ...]
    completed_evidence: tuple[str, ...]
    missing_count: int
    completed_count: int


def build_missing_evidence_projection(
    lifecycle_projection: LifecycleProjection,
) -> MissingEvidenceProjection:
    """Derive the missing-evidence projection purely from a supplied LifecycleProjection.

    Ordering is taken exactly as it appears on the LifecycleProjection's
    own `missing_evidence` and `completed_evidence` tuples -- nothing is
    sorted, inferred, or rediscovered.
    """

    missing_evidence = tuple(
        MissingEvidence(label=label) for label in lifecycle_projection.missing_evidence
    )
    completed_evidence = lifecycle_projection.completed_evidence

    return MissingEvidenceProjection(
        subject_id=lifecycle_projection.subject_id,
        subject_kind=lifecycle_projection.subject_kind,
        missing_evidence=missing_evidence,
        completed_evidence=completed_evidence,
        missing_count=len(missing_evidence),
        completed_count=len(completed_evidence),
    )


def missing_evidence_projection_markdown(projection: MissingEvidenceProjection) -> str:
    """Render one missing evidence projection as deterministic markdown."""

    lines = [
        "# Missing Evidence Projection",
        "",
        "## Subject",
        "",
        "Subject ID:",
        projection.subject_id,
        "",
        "Subject Kind:",
        projection.subject_kind,
        "",
        "## Missing Evidence",
        "",
    ]
    lines.extend(f"* {entry.label}" for entry in projection.missing_evidence)
    lines.extend(["", "## Completed Evidence", ""])
    lines.extend(f"* {label}" for label in projection.completed_evidence)
    lines.extend(
        [
            "",
            "## Counts",
            "",
            "Missing:",
            str(projection.missing_count),
            "",
            "Completed:",
            str(projection.completed_count),
            "",
            "## Provenance Notice",
            "",
            PROVENANCE_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_missing_evidence_projection(
    projection: MissingEvidenceProjection, repo_root: str | Path = "."
) -> str:
    """Write deterministic derived missing-evidence-projection markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "missing_evidence" / f"{projection.subject_id}.missing.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(missing_evidence_projection_markdown(projection), encoding="utf-8")
    return output.relative_to(root).as_posix()
