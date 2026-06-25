"""Leira v4.2 Execution Adapter Contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from leira.dispatcher_kernel.dispatcher import DispatchPlan, dispatch_plan_markdown


@dataclass(frozen=True)
class ExecutionCapability:
    adapter_label: str
    adapter_kind: str
    supported_dispatch_types: tuple[str, ...]
    supported_subject_kinds: tuple[str, ...]
    supported_execution_modes: tuple[str, ...]
    supports_parallel_execution: bool
    supports_dry_run: bool
    supports_interactive_execution: bool

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_label, str):
            raise TypeError("adapter_label must be a string")
        if not self.adapter_label or not self.adapter_label.strip():
            raise ValueError("adapter_label cannot be empty or whitespace-only")

        if not isinstance(self.adapter_kind, str):
            raise TypeError("adapter_kind must be a string")
        if not self.adapter_kind or not self.adapter_kind.strip():
            raise ValueError("adapter_kind cannot be empty or whitespace-only")

        # For collections
        for field_name, value in [
            ("supported_dispatch_types", self.supported_dispatch_types),
            ("supported_subject_kinds", self.supported_subject_kinds),
            ("supported_execution_modes", self.supported_execution_modes),
        ]:
            if not isinstance(value, tuple):
                raise TypeError(f"{field_name} must be a tuple, got {type(value).__name__}")
            for item in value:
                if not isinstance(item, str):
                    raise TypeError(f"Elements of {field_name} must be strings, got {type(item).__name__}")
                if not item or not item.strip():
                    raise ValueError(f"Elements of {field_name} cannot be empty or whitespace-only")

        # For booleans
        for field_name, value in [
            ("supports_parallel_execution", self.supports_parallel_execution),
            ("supports_dry_run", self.supports_dry_run),
            ("supports_interactive_execution", self.supports_interactive_execution),
        ]:
            if not isinstance(value, bool):
                raise TypeError(f"{field_name} must be a boolean, got {type(value).__name__}")


@dataclass(frozen=True)
class ExecutionIntent:
    dispatch_id: str
    adapter_label: str
    dispatch_type: str
    subject_kind: str
    execution_mode: str
    target_label: str

    def __post_init__(self) -> None:
        for field_name in [
            "dispatch_id",
            "adapter_label",
            "dispatch_type",
            "subject_kind",
            "execution_mode",
            "target_label",
        ]:
            val = getattr(self, field_name)
            if not isinstance(val, str):
                raise TypeError(f"{field_name} must be a string, got {type(val).__name__}")
            if not val or not val.strip():
                raise ValueError(f"{field_name} cannot be empty or whitespace-only")


@dataclass(frozen=True)
class ExecutionAdapterResult:
    dispatch_plan: DispatchPlan
    execution_intent: ExecutionIntent
    execution_capability: ExecutionCapability

    def __post_init__(self) -> None:
        if not isinstance(self.dispatch_plan, DispatchPlan):
            raise TypeError(f"dispatch_plan must be a DispatchPlan, got {type(self.dispatch_plan).__name__}")
        if not isinstance(self.execution_intent, ExecutionIntent):
            raise TypeError(f"execution_intent must be an ExecutionIntent, got {type(self.execution_intent).__name__}")
        if not isinstance(self.execution_capability, ExecutionCapability):
            raise TypeError(f"execution_capability must be an ExecutionCapability, got {type(self.execution_capability).__name__}")


def build_execution_intent(dispatch_plan: DispatchPlan, adapter_label: str) -> ExecutionIntent:
    """Build a deterministic ExecutionIntent from a DispatchPlan and adapter_label."""
    if not isinstance(dispatch_plan, DispatchPlan):
        raise TypeError("dispatch_plan must be a DispatchPlan instance")
    if not isinstance(adapter_label, str):
        raise TypeError("adapter_label must be a string")
    if not adapter_label or not adapter_label.strip():
        raise ValueError("adapter_label cannot be empty or whitespace-only")

    return ExecutionIntent(
        dispatch_id=dispatch_plan.dispatch_id,
        adapter_label=adapter_label,
        dispatch_type=dispatch_plan.dispatch_type,
        subject_kind=dispatch_plan.subject_kind,
        execution_mode=dispatch_plan.execution_mode,
        target_label=dispatch_plan.target_label,
    )


def build_execution_adapter_result(
    dispatch_plan: DispatchPlan,
    execution_intent: ExecutionIntent,
    execution_capability: ExecutionCapability,
) -> ExecutionAdapterResult:
    """Build an ExecutionAdapterResult from components without mutation or inference."""
    if not isinstance(dispatch_plan, DispatchPlan):
        raise TypeError("dispatch_plan must be a DispatchPlan instance")
    if not isinstance(execution_intent, ExecutionIntent):
        raise TypeError("execution_intent must be an ExecutionIntent instance")
    if not isinstance(execution_capability, ExecutionCapability):
        raise TypeError("execution_capability must be an ExecutionCapability instance")

    return ExecutionAdapterResult(
        dispatch_plan=dispatch_plan,
        execution_intent=execution_intent,
        execution_capability=execution_capability,
    )


def execution_adapter_contract_markdown(result: ExecutionAdapterResult) -> str:
    """Render execution adapter contract result as deterministic markdown."""
    if not isinstance(result, ExecutionAdapterResult):
        raise TypeError("result must be an ExecutionAdapterResult instance")

    lines = [
        "# Execution Adapter Contract",
        "",
        "## Dispatch Plan",
        "",
        "```text",
        dispatch_plan_markdown(result.dispatch_plan),
        "```",
        "",
    ]

    # Execution Intent
    lines.extend([
        "## Execution Intent",
        "",
        f"* Dispatch ID: {result.execution_intent.dispatch_id}",
        f"* Adapter Label: {result.execution_intent.adapter_label}",
        f"* Dispatch Type: {result.execution_intent.dispatch_type}",
        f"* Subject Kind: {result.execution_intent.subject_kind}",
        f"* Execution Mode: {result.execution_intent.execution_mode}",
        f"* Target Label: {result.execution_intent.target_label}",
        "",
    ])

    # Execution Capability
    cap = result.execution_capability
    cap_lines = [
        "## Execution Capability",
        "",
        f"* Adapter Label: {cap.adapter_label}",
        f"* Adapter Kind: {cap.adapter_kind}",
        "* Supported Dispatch Types:",
    ]
    for dt in cap.supported_dispatch_types:
        cap_lines.append(f"  * {dt}")
    cap_lines.append("* Supported Subject Kinds:")
    for sk in cap.supported_subject_kinds:
        cap_lines.append(f"  * {sk}")
    cap_lines.append("* Supported Execution Modes:")
    for em in cap.supported_execution_modes:
        cap_lines.append(f"  * {em}")
    cap_lines.extend([
        f"* Supports Parallel Execution: {cap.supports_parallel_execution}",
        f"* Supports Dry Run: {cap.supports_dry_run}",
        f"* Supports Interactive Execution: {cap.supports_interactive_execution}",
        "",
    ])
    lines.extend(cap_lines)

    # Provenance Notice
    lines.extend([
        "## Provenance Notice",
        "",
        "> This contract describes what an execution adapter claims it is capable of accepting. It performs no execution, scheduling, planning, orchestration, approval, or dispatch.",
        "",
    ])

    return "\n".join(lines)


def write_execution_adapter_contract(
    result: ExecutionAdapterResult,
    repo_root: str | Path = ".",
) -> str:
    """Write deterministic derived execution adapter contract markdown."""
    if not isinstance(result, ExecutionAdapterResult):
        raise TypeError("result must be an ExecutionAdapterResult instance")

    root = Path(repo_root)
    output = (
        root
        / ".leira"
        / "execution_adapter_contract"
        / f"{result.execution_intent.dispatch_id}.{result.execution_intent.adapter_label}.contract.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    content = execution_adapter_contract_markdown(result)
    output.write_text(content, encoding="utf-8")
    return output.relative_to(root).as_posix()
