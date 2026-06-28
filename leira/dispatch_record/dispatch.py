"""Leira v1.16 dispatch records: intended handoff as evidence, not execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROVENANCE_NOTICE = (
    "This record preserves an intended dispatch as evidence.\n"
    "It performs no execution, tool calls, prompt submission, workflow transition or project-state mutation."
)


@dataclass(frozen=True)
class DispatchRecord:
    dispatch_id: str
    human_decision_id: str
    subject_id: str
    subject_kind: str
    dispatch_type: str
    target_label: str
    execution_mode: str
    reason_codes: tuple[str, ...]
    source_label: str
    dispatch_summary: str


def create_dispatch_record(
    *,
    dispatch_id: str,
    human_decision_id: str,
    subject_id: str,
    subject_kind: str,
    dispatch_type: str,
    target_label: str,
    execution_mode: str,
    reason_codes: list[str] | tuple[str, ...],
    source_label: str,
    dispatch_summary: str,
) -> DispatchRecord:
    """Create one immutable dispatch record from caller-supplied evidence."""

    return DispatchRecord(
        dispatch_id=dispatch_id,
        human_decision_id=human_decision_id,
        subject_id=subject_id,
        subject_kind=subject_kind,
        dispatch_type=dispatch_type,
        target_label=target_label,
        execution_mode=execution_mode,
        reason_codes=tuple(reason_codes),
        source_label=source_label,
        dispatch_summary=dispatch_summary,
    )


def dispatch_record_markdown(record: DispatchRecord) -> str:
    """Render one dispatch record as deterministic markdown."""

    lines = [
        "# Dispatch Record",
        "",
        "## Dispatch ID",
        "",
        record.dispatch_id,
        "",
        "## Human Decision",
        "",
        record.human_decision_id,
        "",
        "## Subject",
        "",
        record.subject_id,
        "",
        "## Subject Kind",
        "",
        record.subject_kind,
        "",
        "## Dispatch Type",
        "",
        record.dispatch_type,
        "",
        "## Target",
        "",
        record.target_label,
        "",
        "## Execution Mode",
        "",
        record.execution_mode,
        "",
        "## Source",
        "",
        record.source_label,
        "",
        "## Reason Codes",
        "",
    ]
    lines.extend(f"* {reason_code}" for reason_code in record.reason_codes)
    lines.extend(
        [
            "",
            "## Dispatch Summary",
            "",
            record.dispatch_summary,
            "",
            "## Provenance Notice",
            "",
            PROVENANCE_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_dispatch_record(record: DispatchRecord, repo_root: str | Path = ".") -> str:
    """Write deterministic derived dispatch-record markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "dispatch_records" / f"{record.dispatch_id}.dispatch.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dispatch_record_markdown(record), encoding="utf-8")
    return output.relative_to(root).as_posix()
