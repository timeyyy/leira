"""Leira v4.4 Execution Adapter Selection Kernel.

This kernel partitions declared execution adapters into compatible and incompatible sets
based on compatibility with a given DispatchPlan. It performs no execution, scheduling,
or planning.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from leira.dispatcher_kernel.dispatcher import DispatchPlan, dispatch_plan_markdown
from leira.execution_adapter_contract.contract import (
    ExecutionCapability,
    check_execution_compatibility,
)


@dataclass(frozen=True)
class AdapterSelection:
    dispatch_plan: DispatchPlan
    compatible_adapters: tuple[ExecutionCapability, ...]
    incompatible_adapters: tuple[ExecutionCapability, ...]
    selection_reason: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.dispatch_plan, DispatchPlan):
            raise TypeError(f"dispatch_plan must be a DispatchPlan, got {type(self.dispatch_plan).__name__}")
        if not isinstance(self.compatible_adapters, tuple):
            raise TypeError(f"compatible_adapters must be a tuple, got {type(self.compatible_adapters).__name__}")
        for item in self.compatible_adapters:
            if not isinstance(item, ExecutionCapability):
                raise TypeError(f"Elements of compatible_adapters must be ExecutionCapability, got {type(item).__name__}")
        if not isinstance(self.incompatible_adapters, tuple):
            raise TypeError(f"incompatible_adapters must be a tuple, got {type(self.incompatible_adapters).__name__}")
        for item in self.incompatible_adapters:
            if not isinstance(item, ExecutionCapability):
                raise TypeError(f"Elements of incompatible_adapters must be ExecutionCapability, got {type(item).__name__}")
        if not isinstance(self.selection_reason, tuple):
            raise TypeError(f"selection_reason must be a tuple, got {type(self.selection_reason).__name__}")
        for item in self.selection_reason:
            if not isinstance(item, str):
                raise TypeError(f"Elements of selection_reason must be str, got {type(item).__name__}")
            if not item or not item.strip():
                raise ValueError("Elements of selection_reason cannot be empty or whitespace-only")


@dataclass(frozen=True)
class AdapterSelectionResult:
    dispatch_plan: DispatchPlan
    adapter_selection: AdapterSelection

    def __post_init__(self) -> None:
        if not isinstance(self.dispatch_plan, DispatchPlan):
            raise TypeError(f"dispatch_plan must be a DispatchPlan, got {type(self.dispatch_plan).__name__}")
        if not isinstance(self.adapter_selection, AdapterSelection):
            raise TypeError(f"adapter_selection must be an AdapterSelection, got {type(self.adapter_selection).__name__}")
        if self.adapter_selection.dispatch_plan != self.dispatch_plan:
            raise ValueError("dispatch_plan does not match adapter_selection.dispatch_plan")


def select_execution_adapters(
    dispatch_plan: DispatchPlan,
    capabilities: tuple[ExecutionCapability, ...],
) -> AdapterSelection:
    """Evaluate compatibility for each capability, partitioning them in original order."""
    if not isinstance(dispatch_plan, DispatchPlan):
        raise TypeError("dispatch_plan must be a DispatchPlan instance")
    if not isinstance(capabilities, tuple):
        raise TypeError("capabilities must be a tuple")
    for item in capabilities:
        if not isinstance(item, ExecutionCapability):
            raise TypeError("Elements of capabilities must be ExecutionCapability instances")

    compatible_list: list[ExecutionCapability] = []
    incompatible_list: list[ExecutionCapability] = []
    selection_reasons: list[str] = []

    for cap in capabilities:
        res = check_execution_compatibility(dispatch_plan, cap)
        if res.compatible:
            compatible_list.append(cap)
            selection_reasons.append(f"{cap.adapter_label}: compatible")
        else:
            incompatible_list.append(cap)
            reasons_str = ", ".join(res.reason_codes)
            selection_reasons.append(f"{cap.adapter_label}: incompatible ({reasons_str})")

    return AdapterSelection(
        dispatch_plan=dispatch_plan,
        compatible_adapters=tuple(compatible_list),
        incompatible_adapters=tuple(incompatible_list),
        selection_reason=tuple(selection_reasons),
    )


def build_adapter_selection_result(
    dispatch_plan: DispatchPlan,
    adapter_selection: AdapterSelection,
) -> AdapterSelectionResult:
    """Pure aggregation of a DispatchPlan and its corresponding AdapterSelection."""
    if not isinstance(dispatch_plan, DispatchPlan):
        raise TypeError("dispatch_plan must be a DispatchPlan instance")
    if not isinstance(adapter_selection, AdapterSelection):
        raise TypeError("adapter_selection must be an AdapterSelection instance")

    return AdapterSelectionResult(
        dispatch_plan=dispatch_plan,
        adapter_selection=adapter_selection,
    )


def adapter_selection_markdown(result: AdapterSelectionResult) -> str:
    """Render the AdapterSelectionResult as deterministic markdown."""
    if not isinstance(result, AdapterSelectionResult):
        raise TypeError("result must be an AdapterSelectionResult instance")

    lines = [
        "# Execution Adapter Selection",
        "",
        "## Dispatch Plan",
        "",
        "```text",
        dispatch_plan_markdown(result.dispatch_plan),
        "```",
        "",
    ]

    # Compatible Adapters
    lines.append("## Compatible Adapters")
    lines.append("")
    if not result.adapter_selection.compatible_adapters:
        lines.append("No compatible adapters found.")
        lines.append("")
    else:
        for cap in result.adapter_selection.compatible_adapters:
            lines.extend([
                f"### {cap.adapter_label}",
                "",
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
            ])

    # Incompatible Adapters
    lines.append("## Incompatible Adapters")
    lines.append("")
    if not result.adapter_selection.incompatible_adapters:
        lines.append("No incompatible adapters found.")
        lines.append("")
    else:
        for cap in result.adapter_selection.incompatible_adapters:
            comp_res = check_execution_compatibility(result.dispatch_plan, cap)
            reasons_str = ", ".join(comp_res.reason_codes)
            lines.extend([
                f"### {cap.adapter_label}",
                "",
                f"* Incompatibility Reason: {reasons_str}",
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
            ])

    # Provenance Notice
    lines.extend([
        "## Provenance Notice",
        "",
        "> This selection kernel partitions adapters into compatible and incompatible sets. It performs no execution, scheduling, planning, orchestration, approval, or dispatch.",
        "",
    ])

    return "\n".join(lines)


def write_adapter_selection(
    result: AdapterSelectionResult,
    repo_root: str | Path = ".",
) -> str:
    """Write deterministic derived execution adapter selection markdown."""
    if not isinstance(result, AdapterSelectionResult):
        raise TypeError("result must be an AdapterSelectionResult instance")

    root = Path(repo_root)
    output = (
        root
        / ".leira"
        / "execution_adapter_selection"
        / f"{result.dispatch_plan.dispatch_id}.selection.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    content = adapter_selection_markdown(result)
    output.write_text(content, encoding="utf-8")
    return output.relative_to(root).as_posix()
