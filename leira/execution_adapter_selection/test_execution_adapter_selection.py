"""Tests for Leira v4.4 Execution Adapter Selection Kernel."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import sys
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from leira.dispatcher_kernel.dispatcher import DispatchPlan
from leira.execution_adapter_contract.contract import ExecutionCapability
from leira.execution_adapter_selection.selection import (
    AdapterSelection,
    AdapterSelectionResult,
    select_execution_adapters,
    build_adapter_selection_result,
    adapter_selection_markdown,
    write_adapter_selection,
)


@pytest.fixture
def sample_dispatch_plan() -> DispatchPlan:
    return DispatchPlan(
        dispatch_id="disp-abc-123",
        subject_id="subj-999",
        subject_kind="codebase",
        dispatch_type="deployment",
        target_label="target_adapter_1",
        execution_mode="interactive",
        reason_codes=("auth_success", "pipeline_green"),
        dispatch_summary="Deploying revision to production.",
    )


@pytest.fixture
def compatible_capability() -> ExecutionCapability:
    return ExecutionCapability(
        adapter_label="target_adapter_1",
        adapter_kind="ssh",
        supported_dispatch_types=("deployment", "verification"),
        supported_subject_kinds=("codebase", "container"),
        supported_execution_modes=("interactive", "dry_run"),
        supports_parallel_execution=True,
        supports_dry_run=True,
        supports_interactive_execution=True,
    )


@pytest.fixture
def incompatible_capability_mismatch_label() -> ExecutionCapability:
    return ExecutionCapability(
        adapter_label="target_adapter_2", # Mismatch
        adapter_kind="ssh",
        supported_dispatch_types=("deployment", "verification"),
        supported_subject_kinds=("codebase", "container"),
        supported_execution_modes=("interactive", "dry_run"),
        supports_parallel_execution=True,
        supports_dry_run=True,
        supports_interactive_execution=True,
    )


@pytest.fixture
def incompatible_capability_mismatch_mode() -> ExecutionCapability:
    return ExecutionCapability(
        adapter_label="target_adapter_1",
        adapter_kind="ssh",
        supported_dispatch_types=("deployment", "verification"),
        supported_subject_kinds=("codebase", "container"),
        supported_execution_modes=("dry_run",), # Mismatch (no interactive)
        supports_parallel_execution=True,
        supports_dry_run=True,
        supports_interactive_execution=True,
    )


def test_immutable_dataclasses(sample_dispatch_plan, compatible_capability):
    """Verify that AdapterSelection and AdapterSelectionResult are frozen/immutable."""
    selection = select_execution_adapters(sample_dispatch_plan, (compatible_capability,))
    result = build_adapter_selection_result(sample_dispatch_plan, selection)

    with pytest.raises(FrozenInstanceError):
        selection.selection_reason = ("new_reason",)  # type: ignore

    with pytest.raises(FrozenInstanceError):
        result.dispatch_plan = sample_dispatch_plan  # type: ignore


def test_type_and_value_validation(sample_dispatch_plan, compatible_capability):
    """Verify that type-checking and constraints are enforced on dataclass construction."""
    # Test AdapterSelection constructor validation
    with pytest.raises(TypeError):
        AdapterSelection(
            dispatch_plan=None,  # type: ignore
            compatible_adapters=(compatible_capability,),
            incompatible_adapters=(),
            selection_reason=("compatible",),
        )

    with pytest.raises(TypeError):
        AdapterSelection(
            dispatch_plan=sample_dispatch_plan,
            compatible_adapters=None,  # type: ignore
            incompatible_adapters=(),
            selection_reason=("compatible",),
        )

    with pytest.raises(TypeError):
        AdapterSelection(
            dispatch_plan=sample_dispatch_plan,
            compatible_adapters=(123,),  # type: ignore
            incompatible_adapters=(),
            selection_reason=("compatible",),
        )

    with pytest.raises(TypeError):
        AdapterSelection(
            dispatch_plan=sample_dispatch_plan,
            compatible_adapters=(compatible_capability,),
            incompatible_adapters=None,  # type: ignore
            selection_reason=("compatible",),
        )

    with pytest.raises(TypeError):
        AdapterSelection(
            dispatch_plan=sample_dispatch_plan,
            compatible_adapters=(compatible_capability,),
            incompatible_adapters=(),
            selection_reason=None,  # type: ignore
        )

    with pytest.raises(TypeError):
        AdapterSelection(
            dispatch_plan=sample_dispatch_plan,
            compatible_adapters=(compatible_capability,),
            incompatible_adapters=(),
            selection_reason=(123,),  # type: ignore
        )

    with pytest.raises(ValueError):
        AdapterSelection(
            dispatch_plan=sample_dispatch_plan,
            compatible_adapters=(compatible_capability,),
            incompatible_adapters=(),
            selection_reason=("   ",),
        )

    # Test AdapterSelectionResult constructor validation
    selection = select_execution_adapters(sample_dispatch_plan, (compatible_capability,))
    
    with pytest.raises(TypeError):
        AdapterSelectionResult(dispatch_plan=None, adapter_selection=selection)  # type: ignore

    with pytest.raises(TypeError):
        AdapterSelectionResult(dispatch_plan=sample_dispatch_plan, adapter_selection=None)  # type: ignore

    # Mismatched dispatch plan check
    other_plan = DispatchPlan(
        dispatch_id="disp-different",
        subject_id="subj-999",
        subject_kind="codebase",
        dispatch_type="deployment",
        target_label="target_adapter_1",
        execution_mode="interactive",
        reason_codes=("auth_success",),
        dispatch_summary="Deploying to another server.",
    )
    with pytest.raises(ValueError):
        AdapterSelectionResult(dispatch_plan=other_plan, adapter_selection=selection)


def test_select_execution_adapters_empty(sample_dispatch_plan):
    """Verify selection with empty input capability list."""
    selection = select_execution_adapters(sample_dispatch_plan, ())
    assert selection.dispatch_plan == sample_dispatch_plan
    assert selection.compatible_adapters == ()
    assert selection.incompatible_adapters == ()
    assert selection.selection_reason == ()


def test_select_execution_adapters_all_compatible(sample_dispatch_plan, compatible_capability):
    """Verify selection when all inputs are compatible."""
    selection = select_execution_adapters(sample_dispatch_plan, (compatible_capability,))
    assert selection.compatible_adapters == (compatible_capability,)
    assert selection.incompatible_adapters == ()
    assert selection.selection_reason == ("target_adapter_1: compatible",)


def test_select_execution_adapters_all_incompatible(
    sample_dispatch_plan,
    incompatible_capability_mismatch_label,
    incompatible_capability_mismatch_mode,
):
    """Verify selection when all inputs are incompatible."""
    capabilities = (incompatible_capability_mismatch_label, incompatible_capability_mismatch_mode)
    selection = select_execution_adapters(sample_dispatch_plan, capabilities)
    
    assert selection.compatible_adapters == ()
    assert selection.incompatible_adapters == capabilities
    assert selection.selection_reason == (
        "target_adapter_2: incompatible (target_label_mismatch)",
        "target_adapter_1: incompatible (unsupported_execution_mode)",
    )


def test_select_execution_adapters_mixed(
    sample_dispatch_plan,
    compatible_capability,
    incompatible_capability_mismatch_label,
    incompatible_capability_mismatch_mode,
):
    """Verify selection behavior with mixed compatibility and caller order preservation."""
    # Preserves order: compat, mismatch_label, mismatch_mode
    capabilities = (
        compatible_capability,
        incompatible_capability_mismatch_label,
        incompatible_capability_mismatch_mode,
    )
    selection = select_execution_adapters(sample_dispatch_plan, capabilities)
    
    assert selection.compatible_adapters == (compatible_capability,)
    assert selection.incompatible_adapters == (
        incompatible_capability_mismatch_label,
        incompatible_capability_mismatch_mode,
    )
    assert selection.selection_reason == (
        "target_adapter_1: compatible",
        "target_adapter_2: incompatible (target_label_mismatch)",
        "target_adapter_1: incompatible (unsupported_execution_mode)",
    )


def test_preserve_caller_ordering(sample_dispatch_plan):
    """Ensure order is exactly preserved without any sorting, scoring, or heuristics."""
    cap1 = ExecutionCapability(
        adapter_label="target_adapter_1",
        adapter_kind="ssh",
        supported_dispatch_types=("deployment",),
        supported_subject_kinds=("codebase",),
        supported_execution_modes=("interactive",),
        supports_parallel_execution=True,
        supports_dry_run=True,
        supports_interactive_execution=True,
    )
    cap2 = ExecutionCapability(
        adapter_label="target_adapter_1",
        adapter_kind="k8s",
        supported_dispatch_types=("deployment",),
        supported_subject_kinds=("codebase",),
        supported_execution_modes=("interactive",),
        supports_parallel_execution=True,
        supports_dry_run=True,
        supports_interactive_execution=True,
    )
    
    capabilities = (cap2, cap1) # reverse order
    selection = select_execution_adapters(sample_dispatch_plan, capabilities)
    
    assert selection.compatible_adapters == (cap2, cap1)


def test_deterministic_markdown(sample_dispatch_plan, compatible_capability, incompatible_capability_mismatch_label):
    """Verify that selection markdown is generated deterministically and contains required sections."""
    capabilities = (compatible_capability, incompatible_capability_mismatch_label)
    selection = select_execution_adapters(sample_dispatch_plan, capabilities)
    result = build_adapter_selection_result(sample_dispatch_plan, selection)
    
    md1 = adapter_selection_markdown(result)
    md2 = adapter_selection_markdown(result)
    
    assert md1 == md2
    
    # Check sections
    assert "# Execution Adapter Selection" in md1
    assert "## Dispatch Plan" in md1
    assert "## Compatible Adapters" in md1
    assert "## Incompatible Adapters" in md1
    assert "## Provenance Notice" in md1
    
    # Check detailed content
    assert "target_adapter_1" in md1
    assert "target_adapter_2" in md1
    assert "target_label_mismatch" in md1


def test_deterministic_file_output(tmp_path, sample_dispatch_plan, compatible_capability, incompatible_capability_mismatch_label):
    """Verify that write_adapter_selection writes correct bytes and paths deterministically."""
    capabilities = (compatible_capability, incompatible_capability_mismatch_label)
    selection = select_execution_adapters(sample_dispatch_plan, capabilities)
    result = build_adapter_selection_result(sample_dispatch_plan, selection)
    
    rel_path1 = write_adapter_selection(result, repo_root=tmp_path)
    rel_path2 = write_adapter_selection(result, repo_root=tmp_path)
    
    assert rel_path1 == rel_path2
    assert rel_path1 == f".leira/execution_adapter_selection/{sample_dispatch_plan.dispatch_id}.selection.md"
    
    full_path = tmp_path / rel_path1
    assert full_path.exists()
    
    content = full_path.read_text(encoding="utf-8")
    assert "# Execution Adapter Selection" in content
    assert "## Compatible Adapters" in content
    
    # Repeated write produces byte-identical output
    bytes_before = full_path.read_bytes()
    write_adapter_selection(result, repo_root=tmp_path)
    bytes_after = full_path.read_bytes()
    assert bytes_before == bytes_after


@patch("time.time")
@patch("random.random")
@patch("uuid.uuid4")
@patch("subprocess.run")
def test_no_non_deterministic_behaviors(
    mock_sub, mock_uuid, mock_rand, mock_time,
    sample_dispatch_plan, compatible_capability
):
    """Assert that selecting adapters makes absolutely no calls to clocks, randomness, UUIDs, or subprocesses."""
    selection = select_execution_adapters(sample_dispatch_plan, (compatible_capability,))
    result = build_adapter_selection_result(sample_dispatch_plan, selection)
    adapter_selection_markdown(result)
    
    assert not mock_time.called
    assert not mock_rand.called
    assert not mock_uuid.called
    assert not mock_sub.called
