"""Leira v4.0 dispatcher kernel: deterministic derivation of execution plans.

This kernel is the final deterministic boundary before outside execution.
It validates a committed DispatchRecord and prepares a DispatchPlan.
It never performs execution, scheduling, state mutations, or tool calls.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from leira.dispatch_record.dispatch import (
    DispatchRecord,
    dispatch_record_markdown,
)

PROVENANCE_NOTICE = (
    "This kernel prepares deterministic execution plans.\n"
    "It performs no execution, planning, scheduling, approval or dispatch."
)


@dataclass(frozen=True)
class DispatchPlan:
    dispatch_id: str
    subject_id: str
    subject_kind: str
    dispatch_type: str
    target_label: str
    execution_mode: str
    reason_codes: tuple[str, ...]
    dispatch_summary: str


@dataclass(frozen=True)
class DispatcherKernelResult:
    dispatch_record: DispatchRecord
    dispatch_plan: DispatchPlan


def build_dispatch_plan(record: DispatchRecord) -> DispatchPlan:
    """Build a deterministic DispatchPlan from a DispatchRecord."""
    if not isinstance(record, DispatchRecord):
        raise TypeError("record must be a DispatchRecord instance")

    return DispatchPlan(
        dispatch_id=record.dispatch_id,
        subject_id=record.subject_id,
        subject_kind=record.subject_kind,
        dispatch_type=record.dispatch_type,
        target_label=record.target_label,
        execution_mode=record.execution_mode,
        reason_codes=record.reason_codes,
        dispatch_summary=record.dispatch_summary,
    )


def run_dispatcher_kernel(record: DispatchRecord) -> DispatcherKernelResult:
    """Validate the DispatchRecord, build the DispatchPlan, and return result."""
    if not isinstance(record, DispatchRecord):
        raise TypeError("record must be a DispatchRecord instance")

    # Type and value checks on DispatchRecord fields
    str_fields = {
        "dispatch_id": record.dispatch_id,
        "human_decision_id": record.human_decision_id,
        "subject_id": record.subject_id,
        "subject_kind": record.subject_kind,
        "dispatch_type": record.dispatch_type,
        "target_label": record.target_label,
        "execution_mode": record.execution_mode,
        "source_label": record.source_label,
        "dispatch_summary": record.dispatch_summary,
    }

    for name, value in str_fields.items():
        if not isinstance(value, str):
            raise TypeError(f"Field '{name}' must be a string, got {type(value).__name__}")
        if not value or not value.strip():
            raise ValueError(f"Field '{name}' cannot be empty or whitespace-only")

    if not isinstance(record.reason_codes, tuple):
        raise TypeError(f"Field 'reason_codes' must be a tuple, got {type(record.reason_codes).__name__}")

    for code in record.reason_codes:
        if not isinstance(code, str):
            raise TypeError(f"Reason codes must be strings, got {type(code).__name__}")

    plan = build_dispatch_plan(record)

    return DispatcherKernelResult(
        dispatch_record=record,
        dispatch_plan=plan,
    )


def dispatch_plan_markdown(plan: DispatchPlan) -> str:
    """Render one dispatch plan as deterministic markdown."""
    lines = [
        "# Dispatch Plan",
        "",
        "## Dispatch ID",
        "",
        plan.dispatch_id,
        "",
        "## Subject",
        "",
        plan.subject_id,
        "",
        "## Subject Kind",
        "",
        plan.subject_kind,
        "",
        "## Dispatch Type",
        "",
        plan.dispatch_type,
        "",
        "## Target",
        "",
        plan.target_label,
        "",
        "## Execution Mode",
        "",
        plan.execution_mode,
        "",
        "## Reason Codes",
        "",
    ]
    lines.extend(f"* {reason_code}" for reason_code in plan.reason_codes)
    lines.extend(
        [
            "",
            "## Dispatch Summary",
            "",
            plan.dispatch_summary,
            "",
        ]
    )
    return "\n".join(lines)


def dispatcher_kernel_markdown(result: DispatcherKernelResult) -> str:
    """Render dispatcher kernel result as deterministic markdown."""
    lines = [
        "# Dispatcher Kernel",
        "",
        "## Dispatch Record",
        "",
        "```text",
        dispatch_record_markdown(result.dispatch_record),
        "```",
        "",
        "## Dispatch Plan",
        "",
        "```text",
        dispatch_plan_markdown(result.dispatch_plan),
        "```",
        "",
        "## Provenance Notice",
        "",
        PROVENANCE_NOTICE,
        "",
    ]
    return "\n".join(lines)


def write_dispatcher_kernel(result: DispatcherKernelResult, repo_root: str | Path = ".") -> str:
    """Write deterministic derived dispatcher-kernel markdown."""
    root = Path(repo_root)
    output = root / ".leira" / "dispatcher_kernel" / f"{result.dispatch_record.dispatch_id}.dispatch_plan.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dispatcher_kernel_markdown(result), encoding="utf-8")
    return output.relative_to(root).as_posix()


@dataclass(frozen=True)
class DispatchPlanReceipt:
    dispatch_id: str
    subject_id: str
    subject_kind: str
    dispatch_type: str
    target_label: str
    execution_mode: str
    reason_codes: tuple[str, ...]
    dispatch_summary: str
    dispatch_plan_path: str
    dispatch_plan_sha256: str
    provenance_notice: str


def build_dispatch_plan_receipt(
    result: DispatcherKernelResult,
    dispatch_plan_path: str,
    dispatch_plan_sha256: str,
) -> DispatchPlanReceipt:
    """Build a deterministic DispatchPlanReceipt from a DispatcherKernelResult."""
    if not isinstance(result, DispatcherKernelResult):
        raise TypeError("result must be a DispatcherKernelResult instance")
    if not isinstance(dispatch_plan_path, str):
        raise TypeError("dispatch_plan_path must be a string")
    if not dispatch_plan_path or not dispatch_plan_path.strip():
        raise ValueError("dispatch_plan_path cannot be empty or whitespace-only")
    if not isinstance(dispatch_plan_sha256, str):
        raise TypeError("dispatch_plan_sha256 must be a string")
    if not dispatch_plan_sha256 or not dispatch_plan_sha256.strip():
        raise ValueError("dispatch_plan_sha256 cannot be empty or whitespace-only")

    plan = result.dispatch_plan
    return DispatchPlanReceipt(
        dispatch_id=plan.dispatch_id,
        subject_id=plan.subject_id,
        subject_kind=plan.subject_kind,
        dispatch_type=plan.dispatch_type,
        target_label=plan.target_label,
        execution_mode=plan.execution_mode,
        reason_codes=plan.reason_codes,
        dispatch_summary=plan.dispatch_summary,
        dispatch_plan_path=dispatch_plan_path,
        dispatch_plan_sha256=dispatch_plan_sha256,
        provenance_notice=PROVENANCE_NOTICE,
    )


def dispatch_plan_receipt_json(receipt: DispatchPlanReceipt) -> str:
    """Return a deterministic JSON representation of the receipt."""
    if not isinstance(receipt, DispatchPlanReceipt):
        raise TypeError("receipt must be a DispatchPlanReceipt instance")

    data = {
        "dispatch_id": receipt.dispatch_id,
        "subject_id": receipt.subject_id,
        "subject_kind": receipt.subject_kind,
        "dispatch_type": receipt.dispatch_type,
        "target_label": receipt.target_label,
        "execution_mode": receipt.execution_mode,
        "reason_codes": list(receipt.reason_codes),
        "dispatch_summary": receipt.dispatch_summary,
        "dispatch_plan_path": receipt.dispatch_plan_path,
        "dispatch_plan_sha256": receipt.dispatch_plan_sha256,
        "provenance_notice": receipt.provenance_notice,
    }
    return json.dumps(data, indent=2) + "\n"


def write_dispatcher_kernel_receipt(
    receipt: DispatchPlanReceipt,
    repo_root: str | Path = ".",
) -> str:
    """Write the deterministic receipt file and return its path relative to repo_root."""
    if not isinstance(receipt, DispatchPlanReceipt):
        raise TypeError("receipt must be a DispatchPlanReceipt instance")

    root = Path(repo_root)
    output = root / ".leira" / "dispatcher_kernel_receipts" / f"{receipt.dispatch_id}.dispatch_plan_receipt.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    content = dispatch_plan_receipt_json(receipt)
    output.write_text(content, encoding="utf-8")
    return output.relative_to(root).as_posix()

