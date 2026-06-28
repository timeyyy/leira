"""Leira v1.18 verification records: observed evidence, not a verdict."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROVENANCE_NOTICE = (
    "This record preserves verification observations as evidence.\n"
    "It does not approve, reject, dispatch, execute or decide next action."
)


@dataclass(frozen=True)
class VerificationRecord:
    verification_id: str
    implementation_report_id: str
    verifier_label: str
    source_label: str
    checks_run: tuple[str, ...]
    observed_results: tuple[str, ...]
    observed_failures: tuple[str, ...]
    observed_files_created: tuple[str, ...]
    observed_files_modified: tuple[str, ...]
    observed_files_deleted: tuple[str, ...]
    commands_observed: tuple[str, ...]
    reason_codes: tuple[str, ...]
    verification_summary: str


def create_verification_record(
    *,
    verification_id: str,
    implementation_report_id: str,
    verifier_label: str,
    source_label: str,
    checks_run: list[str] | tuple[str, ...],
    observed_results: list[str] | tuple[str, ...],
    observed_failures: list[str] | tuple[str, ...],
    observed_files_created: list[str] | tuple[str, ...],
    observed_files_modified: list[str] | tuple[str, ...],
    observed_files_deleted: list[str] | tuple[str, ...],
    commands_observed: list[str] | tuple[str, ...],
    reason_codes: list[str] | tuple[str, ...],
    verification_summary: str,
) -> VerificationRecord:
    """Create one immutable verification record from caller-supplied evidence."""

    return VerificationRecord(
        verification_id=verification_id,
        implementation_report_id=implementation_report_id,
        verifier_label=verifier_label,
        source_label=source_label,
        checks_run=tuple(checks_run),
        observed_results=tuple(observed_results),
        observed_failures=tuple(observed_failures),
        observed_files_created=tuple(observed_files_created),
        observed_files_modified=tuple(observed_files_modified),
        observed_files_deleted=tuple(observed_files_deleted),
        commands_observed=tuple(commands_observed),
        reason_codes=tuple(reason_codes),
        verification_summary=verification_summary,
    )


def verification_record_markdown(record: VerificationRecord) -> str:
    """Render one verification record as deterministic markdown."""

    lines = [
        "# Verification Record",
        "",
        "## Verification ID",
        "",
        record.verification_id,
        "",
        "## Implementation Report",
        "",
        record.implementation_report_id,
        "",
        "## Verifier",
        "",
        record.verifier_label,
        "",
        "## Source",
        "",
        record.source_label,
        "",
        "## Checks Run",
        "",
    ]
    lines.extend(f"* {entry}" for entry in record.checks_run)
    lines.extend(["", "## Observed Results", ""])
    lines.extend(f"* {entry}" for entry in record.observed_results)
    lines.extend(["", "## Observed Failures", ""])
    lines.extend(f"* {entry}" for entry in record.observed_failures)
    lines.extend(["", "## Observed Files Created", ""])
    lines.extend(f"* {entry}" for entry in record.observed_files_created)
    lines.extend(["", "## Observed Files Modified", ""])
    lines.extend(f"* {entry}" for entry in record.observed_files_modified)
    lines.extend(["", "## Observed Files Deleted", ""])
    lines.extend(f"* {entry}" for entry in record.observed_files_deleted)
    lines.extend(["", "## Commands Observed", ""])
    lines.extend(f"* {entry}" for entry in record.commands_observed)
    lines.extend(["", "## Reason Codes", ""])
    lines.extend(f"* {entry}" for entry in record.reason_codes)
    lines.extend(
        [
            "",
            "## Verification Summary",
            "",
            record.verification_summary,
            "",
            "## Provenance Notice",
            "",
            PROVENANCE_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_verification_record(record: VerificationRecord, repo_root: str | Path = ".") -> str:
    """Write deterministic derived verification-record markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "verification_records" / f"{record.verification_id}.verification.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(verification_record_markdown(record), encoding="utf-8")
    return output.relative_to(root).as_posix()
