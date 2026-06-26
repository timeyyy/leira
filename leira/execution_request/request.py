"""Leira v4.5 Execution Request Layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union

from leira.dispatcher_kernel.dispatcher import DispatchPlan, dispatch_plan_markdown
from leira.execution_adapter_contract.contract import (
    ExecutionCapability,
    check_execution_compatibility,
)
from leira.execution_adapter_selection.selection import AdapterSelection


class IncompatibleCapabilityError(ValueError):
    """Raised when an ExecutionCapability is incompatible with a DispatchPlan."""


@dataclass(frozen=True)
class ExecutionRequest:
    dispatch_plan: DispatchPlan
    execution_capability: ExecutionCapability
    adapter_label: str
    dispatch_id: str
    request_summary: str

    def __post_init__(self) -> None:
        if not isinstance(self.dispatch_plan, DispatchPlan):
            raise TypeError(
                f"dispatch_plan must be a DispatchPlan, got {type(self.dispatch_plan).__name__}"
            )
        if not isinstance(self.execution_capability, ExecutionCapability):
            raise TypeError(
                f"execution_capability must be an ExecutionCapability, got {type(self.execution_capability).__name__}"
            )
        if not isinstance(self.adapter_label, str):
            raise TypeError(
                f"adapter_label must be a string, got {type(self.adapter_label).__name__}"
            )
        if not self.adapter_label or not self.adapter_label.strip():
            raise ValueError("adapter_label cannot be empty or whitespace-only")
        if not isinstance(self.dispatch_id, str):
            raise TypeError(
                f"dispatch_id must be a string, got {type(self.dispatch_id).__name__}"
            )
        if not self.dispatch_id or not self.dispatch_id.strip():
            raise ValueError("dispatch_id cannot be empty or whitespace-only")
        if not isinstance(self.request_summary, str):
            raise TypeError(
                f"request_summary must be a string, got {type(self.request_summary).__name__}"
            )
        if not self.request_summary or not self.request_summary.strip():
            raise ValueError("request_summary cannot be empty or whitespace-only")

        if self.dispatch_plan.dispatch_id != self.dispatch_id:
            raise ValueError(
                f"dispatch_id mismatch: {self.dispatch_id} vs {self.dispatch_plan.dispatch_id}"
            )
        if self.execution_capability.adapter_label != self.adapter_label:
            raise ValueError(
                f"adapter_label mismatch: {self.adapter_label} vs {self.execution_capability.adapter_label}"
            )


@dataclass(frozen=True)
class ExecutionRequestResult:
    adapter_selection: AdapterSelection
    execution_request: ExecutionRequest

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_selection, AdapterSelection):
            raise TypeError(
                f"adapter_selection must be an AdapterSelection, got {type(self.adapter_selection).__name__}"
            )
        if not isinstance(self.execution_request, ExecutionRequest):
            raise TypeError(
                f"execution_request must be an ExecutionRequest, got {type(self.execution_request).__name__}"
            )

        if self.execution_request.dispatch_plan != self.adapter_selection.dispatch_plan:
            raise ValueError("dispatch_plan mismatch between execution_request and adapter_selection")

        if self.execution_request.execution_capability not in self.adapter_selection.compatible_adapters:
            raise ValueError(
                f"execution_capability '{self.execution_request.adapter_label}' "
                "is not listed under compatible_adapters in the selection"
            )


def build_execution_request(
    dispatch_plan: DispatchPlan,
    execution_capability: ExecutionCapability,
) -> ExecutionRequest:
    """Build a deterministic ExecutionRequest from a DispatchPlan and ExecutionCapability."""
    if not isinstance(dispatch_plan, DispatchPlan):
        raise TypeError("dispatch_plan must be a DispatchPlan instance")
    if not isinstance(execution_capability, ExecutionCapability):
        raise TypeError("execution_capability must be an ExecutionCapability instance")

    compat_res = check_execution_compatibility(dispatch_plan, execution_capability)
    if not compat_res.compatible:
        reasons = ", ".join(compat_res.reason_codes)
        raise IncompatibleCapabilityError(
            f"ExecutionCapability '{execution_capability.adapter_label}' is incompatible "
            f"with DispatchPlan '{dispatch_plan.dispatch_id}': {reasons}"
        )

    summary = (
        f"Execution request for dispatch plan '{dispatch_plan.dispatch_id}' "
        f"using compatible adapter '{execution_capability.adapter_label}'."
    )

    return ExecutionRequest(
        dispatch_plan=dispatch_plan,
        execution_capability=execution_capability,
        adapter_label=execution_capability.adapter_label,
        dispatch_id=dispatch_plan.dispatch_id,
        request_summary=summary,
    )


def build_execution_request_result(
    adapter_selection: AdapterSelection,
    execution_request: ExecutionRequest,
) -> ExecutionRequestResult:
    """Pure aggregation of AdapterSelection and ExecutionRequest."""
    if not isinstance(adapter_selection, AdapterSelection):
        raise TypeError("adapter_selection must be an AdapterSelection instance")
    if not isinstance(execution_request, ExecutionRequest):
        raise TypeError("execution_request must be an ExecutionRequest instance")

    return ExecutionRequestResult(
        adapter_selection=adapter_selection,
        execution_request=execution_request,
    )


def execution_request_markdown(
    request: Union[ExecutionRequest, ExecutionRequestResult]
) -> str:
    """Render execution request as deterministic markdown."""
    if isinstance(request, ExecutionRequestResult):
        req = request.execution_request
    elif isinstance(request, ExecutionRequest):
        req = request
    else:
        raise TypeError("request must be an ExecutionRequest or ExecutionRequestResult instance")

    lines = [
        "# Execution Request",
        "",
        "## Dispatch Plan",
        "",
        "```text",
        dispatch_plan_markdown(req.dispatch_plan),
        "```",
        "",
        "## Execution Capability",
        "",
    ]

    cap = req.execution_capability
    lines.extend([
        f"* Adapter Label: {cap.adapter_label}",
        f"* Adapter Kind: {cap.adapter_kind}",
        "* Supported Dispatch Types:",
    ])
    for dt in cap.supported_dispatch_types:
        lines.append(f"  * {dt}")
    lines.append("* Supported Subject Kinds:")
    for sk in cap.supported_subject_kinds:
        lines.append(f"  * {sk}")
    lines.append("* Supported Execution Modes:")
    for em in cap.supported_execution_modes:
        lines.append(f"  * {em}")
    lines.extend([
        f"* Supports Parallel Execution: {cap.supports_parallel_execution}",
        f"* Supports Dry Run: {cap.supports_dry_run}",
        f"* Supports Interactive Execution: {cap.supports_interactive_execution}",
        "",
        "## Execution Request",
        "",
        f"* Dispatch ID: {req.dispatch_id}",
        f"* Adapter Label: {req.adapter_label}",
        f"* Request Summary: {req.request_summary}",
        "",
        "## Provenance Notice",
        "",
        "> This request describes exactly what an execution adapter would receive. It performs no execution, scheduling, planning, orchestration, approval, or dispatch.",
        "",
    ])

    return "\n".join(lines)


def write_execution_request(
    request: Union[ExecutionRequest, ExecutionRequestResult],
    repo_root: str | Path = ".",
) -> str:
    """Write deterministic derived execution request markdown."""
    if isinstance(request, ExecutionRequestResult):
        req = request.execution_request
    elif isinstance(request, ExecutionRequest):
        req = request
    else:
        raise TypeError("request must be an ExecutionRequest or ExecutionRequestResult instance")

    root = Path(repo_root)
    output = (
        root
        / ".leira"
        / "execution_request"
        / f"{req.dispatch_id}.{req.adapter_label}.request.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    content = execution_request_markdown(req)
    output.write_text(content, encoding="utf-8")
    return output.relative_to(root).as_posix()
