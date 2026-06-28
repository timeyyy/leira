"""Leira v1.17 implementation report records: what the implementer claimed, not whether it is true."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROVENANCE_NOTICE = (
    "This record preserves an implementation report as evidence.\n"
    "It does not verify, approve, execute, reconcile or infer success."
)


@dataclass(frozen=True)
class ImplementationReport:
    report_id: str
    dispatch_id: str
    implementer_label: str
    source_label: str
    files_created: tuple[str, ...]
    files_modified: tuple[str, ...]
    files_deleted: tuple[str, ...]
    commands_run: tuple[str, ...]
    reported_results: tuple[str, ...]
    reported_blockers: tuple[str, ...]
    reason_codes: tuple[str, ...]
    report_body: str


def create_implementation_report(
    *,
    report_id: str,
    dispatch_id: str,
    implementer_label: str,
    source_label: str,
    files_created: list[str] | tuple[str, ...],
    files_modified: list[str] | tuple[str, ...],
    files_deleted: list[str] | tuple[str, ...],
    commands_run: list[str] | tuple[str, ...],
    reported_results: list[str] | tuple[str, ...],
    reported_blockers: list[str] | tuple[str, ...],
    reason_codes: list[str] | tuple[str, ...],
    report_body: str,
) -> ImplementationReport:
    """Create one immutable implementation report from caller-supplied evidence."""

    return ImplementationReport(
        report_id=report_id,
        dispatch_id=dispatch_id,
        implementer_label=implementer_label,
        source_label=source_label,
        files_created=tuple(files_created),
        files_modified=tuple(files_modified),
        files_deleted=tuple(files_deleted),
        commands_run=tuple(commands_run),
        reported_results=tuple(reported_results),
        reported_blockers=tuple(reported_blockers),
        reason_codes=tuple(reason_codes),
        report_body=report_body,
    )


def implementation_report_markdown(report: ImplementationReport) -> str:
    """Render one implementation report as deterministic markdown."""

    lines = [
        "# Implementation Report Record",
        "",
        "## Report ID",
        "",
        report.report_id,
        "",
        "## Dispatch",
        "",
        report.dispatch_id,
        "",
        "## Implementer",
        "",
        report.implementer_label,
        "",
        "## Source",
        "",
        report.source_label,
        "",
        "## Files Created",
        "",
    ]
    lines.extend(f"* {entry}" for entry in report.files_created)
    lines.extend(["", "## Files Modified", ""])
    lines.extend(f"* {entry}" for entry in report.files_modified)
    lines.extend(["", "## Files Deleted", ""])
    lines.extend(f"* {entry}" for entry in report.files_deleted)
    lines.extend(["", "## Commands Run", ""])
    lines.extend(f"* {entry}" for entry in report.commands_run)
    lines.extend(["", "## Reported Results", ""])
    lines.extend(f"* {entry}" for entry in report.reported_results)
    lines.extend(["", "## Reported Blockers", ""])
    lines.extend(f"* {entry}" for entry in report.reported_blockers)
    lines.extend(["", "## Reason Codes", ""])
    lines.extend(f"* {entry}" for entry in report.reason_codes)
    lines.extend(
        [
            "",
            "## Report Body",
            "",
            report.report_body,
            "",
            "## Provenance Notice",
            "",
            PROVENANCE_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_implementation_report(report: ImplementationReport, repo_root: str | Path = ".") -> str:
    """Write deterministic derived implementation-report markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "implementation_reports" / f"{report.report_id}.implementation.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(implementation_report_markdown(report), encoding="utf-8")
    return output.relative_to(root).as_posix()
