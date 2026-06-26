"""Tests for Leira v4.5 Execution Manifest Layer."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

from leira.dispatcher_kernel.dispatcher import DispatchPlan
from leira.execution_adapter_contract.contract import ExecutionCapability
from leira.execution_adapter_selection.selection import (
    AdapterSelection,
    select_execution_adapters,
)
from leira.execution_manifest.manifest import (
    ExecutionManifest,
    ExecutionManifestResult,
    IncompatibleCapabilityError,
    build_execution_manifest,
    build_execution_manifest_result,
    execution_manifest_markdown,
    write_execution_manifest,
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
def incompatible_capability() -> ExecutionCapability:
    return ExecutionCapability(
        adapter_label="target_adapter_1",
        adapter_kind="ssh",
        supported_dispatch_types=("verification",),  # Mismatch (no deployment)
        supported_subject_kinds=("codebase", "container"),
        supported_execution_modes=("interactive", "dry_run"),
        supports_parallel_execution=True,
        supports_dry_run=True,
        supports_interactive_execution=True,
    )


def test_immutable_dataclasses(sample_dispatch_plan, compatible_capability):
    """Verify that ExecutionManifest and ExecutionManifestResult are frozen/immutable."""
    manifest = build_execution_manifest(sample_dispatch_plan, compatible_capability)
    selection = select_execution_adapters(sample_dispatch_plan, (compatible_capability,))
    result = build_execution_manifest_result(selection, manifest)

    with pytest.raises(FrozenInstanceError):
        manifest.manifest_summary = "new summary"  # type: ignore

    with pytest.raises(FrozenInstanceError):
        result.execution_manifest = manifest  # type: ignore


def test_validation_types_and_constraints(sample_dispatch_plan, compatible_capability):
    """Verify that type-checking and constraints are enforced on dataclass construction."""
    with pytest.raises(TypeError):
        ExecutionManifest(
            dispatch_plan=None,  # type: ignore
            execution_capability=compatible_capability,
            dispatch_id="disp-abc-123",
            adapter_label="target_adapter_1",
            manifest_summary="summary",
        )

    with pytest.raises(ValueError):
        ExecutionManifest(
            dispatch_plan=sample_dispatch_plan,
            execution_capability=compatible_capability,
            dispatch_id="disp-abc-123",
            adapter_label=" ",
            manifest_summary="summary",
        )

    with pytest.raises(ValueError):
        ExecutionManifest(
            dispatch_plan=sample_dispatch_plan,
            execution_capability=compatible_capability,
            dispatch_id="different-id",
            adapter_label="target_adapter_1",
            manifest_summary="summary",
        )

    with pytest.raises(ValueError):
        ExecutionManifest(
            dispatch_plan=sample_dispatch_plan,
            execution_capability=compatible_capability,
            dispatch_id="disp-abc-123",
            adapter_label="different-label",
            manifest_summary="summary",
        )


def test_build_execution_manifest_compatible(sample_dispatch_plan, compatible_capability):
    """Verify build_execution_manifest with compatible capability generates correct manifest."""
    manifest = build_execution_manifest(sample_dispatch_plan, compatible_capability)
    assert manifest.dispatch_plan == sample_dispatch_plan
    assert manifest.execution_capability == compatible_capability
    assert manifest.adapter_label == compatible_capability.adapter_label
    assert manifest.dispatch_id == sample_dispatch_plan.dispatch_id
    assert "Execution manifest for" in manifest.manifest_summary


def test_build_execution_manifest_incompatible(sample_dispatch_plan, incompatible_capability):
    """Verify build_execution_manifest with incompatible capability raises IncompatibleCapabilityError."""
    assert issubclass(IncompatibleCapabilityError, ValueError)

    with pytest.raises(IncompatibleCapabilityError) as exc_info:
        build_execution_manifest(sample_dispatch_plan, incompatible_capability)

    assert "is incompatible with DispatchPlan" in str(exc_info.value)
    assert "unsupported_dispatch_type" in str(exc_info.value)


def test_build_execution_manifest_result(sample_dispatch_plan, compatible_capability):
    """Verify building and validating ExecutionManifestResult."""
    manifest = build_execution_manifest(sample_dispatch_plan, compatible_capability)
    selection = select_execution_adapters(sample_dispatch_plan, (compatible_capability,))
    result = build_execution_manifest_result(selection, manifest)

    assert result.adapter_selection == selection
    assert result.execution_manifest == manifest

    other_plan = DispatchPlan(
        dispatch_id="other-id",
        subject_id="subj-999",
        subject_kind="codebase",
        dispatch_type="deployment",
        target_label="target_adapter_1",
        execution_mode="interactive",
        reason_codes=("auth_success",),
        dispatch_summary="Other description",
    )
    other_selection = select_execution_adapters(other_plan, (compatible_capability,))
    with pytest.raises(ValueError) as exc_info:
        build_execution_manifest_result(other_selection, manifest)
    assert "dispatch_plan mismatch" in str(exc_info.value)

    empty_selection = select_execution_adapters(sample_dispatch_plan, ())
    with pytest.raises(ValueError) as exc_info:
        build_execution_manifest_result(empty_selection, manifest)
    assert "is not listed under compatible_adapters" in str(exc_info.value)


def test_deterministic_markdown(sample_dispatch_plan, compatible_capability):
    """Verify markdown formatting is deterministic and contains correct sections."""
    manifest = build_execution_manifest(sample_dispatch_plan, compatible_capability)
    md1 = execution_manifest_markdown(manifest)
    md2 = execution_manifest_markdown(manifest)

    assert md1 == md2

    assert "# Execution Manifest" in md1
    assert "## Dispatch Plan" in md1
    assert "## Execution Capability" in md1
    assert "## Manifest" in md1
    assert "## Provenance Notice" in md1

    assert "disp-abc-123" in md1
    assert "target_adapter_1" in md1
    assert "Deploying revision to production." in md1
    assert "This manifest is the final deterministic artifact before execution." in md1


def test_deterministic_file_output(tmp_path, sample_dispatch_plan, compatible_capability):
    """Verify write_execution_manifest writes files and paths deterministically."""
    manifest = build_execution_manifest(sample_dispatch_plan, compatible_capability)

    rel_path1 = write_execution_manifest(manifest, repo_root=tmp_path)
    rel_path2 = write_execution_manifest(manifest, repo_root=tmp_path)

    assert rel_path1 == rel_path2
    expected_path = f".leira/execution_manifest/{sample_dispatch_plan.dispatch_id}.{compatible_capability.adapter_label}.manifest.md"
    assert rel_path1 == expected_path

    full_path = tmp_path / rel_path1
    assert full_path.exists()

    content = full_path.read_text(encoding="utf-8")
    assert "# Execution Manifest" in content

    bytes1 = full_path.read_bytes()
    write_execution_manifest(manifest, repo_root=tmp_path)
    bytes2 = full_path.read_bytes()
    assert bytes1 == bytes2


@patch("time.time")
@patch("random.random")
@patch("uuid.uuid4")
@patch("subprocess.run")
def test_guardrails_no_non_determinism(
    mock_sub, mock_uuid, mock_rand, mock_time,
    sample_dispatch_plan, compatible_capability
):
    """Assert that building/rendering/writing makes no non-deterministic or side-effect calls."""
    manifest = build_execution_manifest(sample_dispatch_plan, compatible_capability)
    selection = select_execution_adapters(sample_dispatch_plan, (compatible_capability,))
    res = build_execution_manifest_result(selection, manifest)

    execution_manifest_markdown(manifest)
    execution_manifest_markdown(res)

    assert not mock_time.called
    assert not mock_rand.called
    assert not mock_uuid.called
    assert not mock_sub.called
