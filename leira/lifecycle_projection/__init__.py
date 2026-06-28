"""Deterministic lifecycle projection: a read model over existing evidence."""

from .lifecycle import (
    EVIDENCE_LABELS,
    PROVENANCE_NOTICE,
    EvidencePresence,
    LifecycleProjection,
    build_lifecycle_projection,
    evidence_presence_table,
    lifecycle_projection_markdown,
    write_lifecycle_projection,
)

__all__ = [
    "EVIDENCE_LABELS",
    "PROVENANCE_NOTICE",
    "EvidencePresence",
    "LifecycleProjection",
    "build_lifecycle_projection",
    "evidence_presence_table",
    "lifecycle_projection_markdown",
    "write_lifecycle_projection",
]
