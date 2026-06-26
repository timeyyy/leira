"""Leira v4.5 Execution Manifest Layer."""

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
class ExecutionManifest:
    dispatch_plan: DispatchPlan
    execution_capability: ExecutionCapability
    dispatch_id: str
    adapter_label: str
    manifest_summary: str

    def __post_init__(self) -> None:
        if not isinstance(self.dispatch_plan, DispatchPlan):
            raise TypeError(
                f"dispatch_plan must be a DispatchPlan, got {type(self.dispatch_plan).__name__}"
            )
        if not isinstance(self.execution_capability, ExecutionCapability):
            raise TypeError(
                f"execution_capability must be an ExecutionCapability, got {type(self.execution_capability).__name__}"
            )
        if not isinstance(self.dispatch_id, str):
            raise TypeError(
                f"dispatch_id must be a string, got {type(self.dispatch_id).__name__}"
            )
        if not self.dispatch_id or not self.dispatch_id.strip():
            raise ValueError("dispatch_id cannot be empty or whitespace-only")
        if not isinstance(self.adapter_label, str):
            raise TypeError(
                f"adapter_label must be a string, got {type(self.adapter_label).__name__}"
            )
        if not self.adapter_label or not self.adapter_label.strip():
            raise ValueError("adapter_label cannot be empty or whitespace-only")
        if not isinstance(self.manifest_summary, str):
            raise TypeError(
                f"manifest_summary must be a string, got {type(self.manifest_summary).__name__}"
            )
        if not self.manifest_summary or not self.manifest_summary.strip():
            raise ValueError("manifest_summary cannot be empty or whitespace-only")

        if self.dispatch_plan.dispatch_id != self.dispatch_id:
            raise ValueError(
                f"dispatch_id mismatch: {self.dispatch_id} vs {self.dispatch_plan.dispatch_id}"
            )
        if self.execution_capability.adapter_label != self.adapter_label:
            raise ValueError(
                f"adapter_label mismatch: {self.adapter_label} vs {self.execution_capability.adapter_label}"
            )


@dataclass(frozen=True)
class ExecutionManifestResult:
    adapter_selection: AdapterSelection
    execution_manifest: ExecutionManifest

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_selection, AdapterSelection):
            raise TypeError(
                f"adapter_selection must be an AdapterSelection, got {type(self.adapter_selection).__name__}"
            )
        if not isinstance(self.execution_manifest, ExecutionManifest):
            raise TypeError(
                f"execution_manifest must be an ExecutionManifest, got {type(self.execution_manifest).__name__}"
            )

        if self.execution_manifest.dispatch_plan != self.adapter_selection.dispatch_plan:
            raise ValueError("dispatch_plan mismatch between execution_manifest and adapter_selection")

        if self.execution_manifest.execution_capability not in self.adapter_selection.compatible_adapters:
            raise ValueError(
                f"execution_capability '{self.execution_manifest.adapter_label}' "
                "is not listed under compatible_adapters in the selection"
            )


def build_execution_manifest(
    dispatch_plan: DispatchPlan,
    execution_capability: ExecutionCapability,
) -> ExecutionManifest:
    """Build a deterministic ExecutionManifest from a DispatchPlan and ExecutionCapability."""
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
        f"Execution manifest for dispatch plan '{dispatch_plan.dispatch_id}' "
        f"using compatible adapter '{execution_capability.adapter_label}'."
    )

    return ExecutionManifest(
        dispatch_plan=dispatch_plan,
        execution_capability=execution_capability,
        dispatch_id=dispatch_plan.dispatch_id,
        adapter_label=execution_capability.adapter_label,
        manifest_summary=summary,
    )


def build_execution_manifest_result(
    adapter_selection: AdapterSelection,
    execution_manifest: ExecutionManifest,
) -> ExecutionManifestResult:
    """Pure aggregation of AdapterSelection and ExecutionManifest."""
    if not isinstance(adapter_selection, AdapterSelection):
        raise TypeError("adapter_selection must be an AdapterSelection instance")
    if not isinstance(execution_manifest, ExecutionManifest):
        raise TypeError("execution_manifest must be an ExecutionManifest instance")

    return ExecutionManifestResult(
        adapter_selection=adapter_selection,
        execution_manifest=execution_manifest,
    )


def execution_manifest_markdown(
    manifest: Union[ExecutionManifest, ExecutionManifestResult]
) -> str:
    """Render execution manifest as deterministic markdown."""
    if isinstance(manifest, ExecutionManifestResult):
        man = manifest.execution_manifest
    elif isinstance(manifest, ExecutionManifest):
        man = manifest
    else:
        raise TypeError("manifest must be an ExecutionManifest or ExecutionManifestResult instance")

    lines = [
        "# Execution Manifest",
        "",
        "## Dispatch Plan",
        "",
        "```text",
        dispatch_plan_markdown(man.dispatch_plan),
        "```",
        "",
        "## Execution Capability",
        "",
    ]

    cap = man.execution_capability
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
        "## Manifest",
        "",
        f"* Dispatch ID: {man.dispatch_id}",
        f"* Adapter Label: {man.adapter_label}",
        f"* Manifest Summary: {man.manifest_summary}",
        "",
        "## Provenance Notice",
        "",
        "> This manifest is the final deterministic artifact before execution. It performs no execution, scheduling, planning, orchestration, approval, or dispatch.",
        "",
    ])

    return "\n".join(lines)


def write_execution_manifest(
    manifest: Union[ExecutionManifest, ExecutionManifestResult],
    repo_root: str | Path = ".",
) -> str:
    """Write deterministic derived execution manifest markdown."""
    if isinstance(manifest, ExecutionManifestResult):
        man = manifest.execution_manifest
    elif isinstance(manifest, ExecutionManifest):
        man = manifest
    else:
        raise TypeError("manifest must be an ExecutionManifest or ExecutionManifestResult instance")

    root = Path(repo_root)
    output = (
        root
        / ".leira"
        / "execution_manifest"
        / f"{man.dispatch_id}.{man.adapter_label}.manifest.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    content = execution_manifest_markdown(man)
    output.write_text(content, encoding="utf-8")
    return output.relative_to(root).as_posix()
