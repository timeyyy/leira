"""Tests for Leira v4.2 Execution Adapter Contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import sys
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from leira.dispatcher_kernel.dispatcher import DispatchPlan
from leira.execution_adapter_contract.contract import (
    ExecutionCapability,
    ExecutionIntent,
    ExecutionAdapterResult,
    build_execution_intent,
    build_execution_adapter_result,
    execution_adapter_contract_markdown,
    write_execution_adapter_contract,
)


@pytest.fixture
def sample_dispatch_plan() -> DispatchPlan:
    return DispatchPlan(
        dispatch_id="disp-abc-123",
        subject_id="subj-999",
        subject_kind="codebase",
        dispatch_type="deployment",
        target_label="production_server",
        execution_mode="interactive",
        reason_codes=("auth_success", "pipeline_green"),
        dispatch_summary="Deploying revision to production.",
    )


@pytest.fixture
def sample_execution_capability() -> ExecutionCapability:
    return ExecutionCapability(
        adapter_label="ssh_deployer",
        adapter_kind="ssh",
        supported_dispatch_types=("deployment", "verification"),
        supported_subject_kinds=("codebase", "container"),
        supported_execution_modes=("interactive", "dry_run"),
        supports_parallel_execution=True,
        supports_dry_run=True,
        supports_interactive_execution=True,
    )


def test_immutable_dataclasses(sample_dispatch_plan, sample_execution_capability):
    """Verify that all three dataclasses are frozen/immutable."""
    intent = build_execution_intent(sample_dispatch_plan, "ssh_deployer")
    result = build_execution_adapter_result(
        sample_dispatch_plan, intent, sample_execution_capability
    )

    with pytest.raises(FrozenInstanceError):
        intent.adapter_label = "new_label"  # type: ignore

    with pytest.raises(FrozenInstanceError):
        sample_execution_capability.adapter_label = "new_label"  # type: ignore

    with pytest.raises(FrozenInstanceError):
        result.dispatch_plan = sample_dispatch_plan  # type: ignore


def test_type_and_value_validation(sample_dispatch_plan, sample_execution_capability):
    """Verify that type-checking and non-empty constraints are enforced."""
    # Test build_execution_intent validation
    with pytest.raises(TypeError):
        build_execution_intent(None, "label")  # type: ignore

    with pytest.raises(TypeError):
        build_execution_intent(sample_dispatch_plan, 123)  # type: ignore

    with pytest.raises(ValueError):
        build_execution_intent(sample_dispatch_plan, "")

    with pytest.raises(ValueError):
        build_execution_intent(sample_dispatch_plan, "   ")

    # Test build_execution_adapter_result validation
    intent = build_execution_intent(sample_dispatch_plan, "ssh_deployer")
    with pytest.raises(TypeError):
        build_execution_adapter_result(None, intent, sample_execution_capability)  # type: ignore

    with pytest.raises(TypeError):
        build_execution_adapter_result(sample_dispatch_plan, None, sample_execution_capability)  # type: ignore

    with pytest.raises(TypeError):
        build_execution_adapter_result(sample_dispatch_plan, intent, None)  # type: ignore

    # Test ExecutionCapability validation for invalid field types
    with pytest.raises(TypeError):
        ExecutionCapability(
            adapter_label=123,  # type: ignore
            adapter_kind="ssh",
            supported_dispatch_types=(),
            supported_subject_kinds=(),
            supported_execution_modes=(),
            supports_parallel_execution=True,
            supports_dry_run=True,
            supports_interactive_execution=True,
        )

    with pytest.raises(TypeError):
        ExecutionCapability(
            adapter_label="ssh_deployer",
            adapter_kind="ssh",
            supported_dispatch_types=("test", 123),  # type: ignore
            supported_subject_kinds=(),
            supported_execution_modes=(),
            supports_parallel_execution=True,
            supports_dry_run=True,
            supports_interactive_execution=True,
        )

    with pytest.raises(TypeError):
        ExecutionCapability(
            adapter_label="ssh_deployer",
            adapter_kind="ssh",
            supported_dispatch_types=(),
            supported_subject_kinds=(),
            supported_execution_modes=(),
            supports_parallel_execution="yes",  # type: ignore
            supports_dry_run=True,
            supports_interactive_execution=True,
        )


def test_deterministic_intent_and_result(sample_dispatch_plan, sample_execution_capability):
    """Verify that intent and result creation is completely deterministic and preserves inputs."""
    intent1 = build_execution_intent(sample_dispatch_plan, "ssh_deployer")
    intent2 = build_execution_intent(sample_dispatch_plan, "ssh_deployer")
    assert intent1 == intent2

    assert intent1.dispatch_id == sample_dispatch_plan.dispatch_id
    assert intent1.adapter_label == "ssh_deployer"
    assert intent1.dispatch_type == sample_dispatch_plan.dispatch_type
    assert intent1.subject_kind == sample_dispatch_plan.subject_kind
    assert intent1.execution_mode == sample_dispatch_plan.execution_mode
    assert intent1.target_label == sample_dispatch_plan.target_label

    result1 = build_execution_adapter_result(
        sample_dispatch_plan, intent1, sample_execution_capability
    )
    result2 = build_execution_adapter_result(
        sample_dispatch_plan, intent2, sample_execution_capability
    )
    assert result1 == result2

    assert result1.dispatch_plan is sample_dispatch_plan
    assert result1.execution_intent is intent1
    assert result1.execution_capability is sample_execution_capability


def test_deterministic_markdown(sample_dispatch_plan, sample_execution_capability):
    """Verify markdown output sections, provenance notice blockquote, and byte-identical repeated rendering."""
    intent = build_execution_intent(sample_dispatch_plan, "ssh_deployer")
    result = build_execution_adapter_result(
        sample_dispatch_plan, intent, sample_execution_capability
    )

    md1 = execution_adapter_contract_markdown(result)
    md2 = execution_adapter_contract_markdown(result)
    assert md1 == md2
    assert md1.encode("utf-8") == md2.encode("utf-8")

    # Assert stable order of sections
    sections = [
        "# Execution Adapter Contract",
        "## Dispatch Plan",
        "## Execution Intent",
        "## Execution Capability",
        "## Provenance Notice",
    ]
    for section in sections:
        assert section in md1

    # Verify positions to guarantee exact ordering
    positions = [md1.find(sec) for sec in sections]
    assert sorted(positions) == positions
    assert -1 not in positions

    # Verify provenance notice blockquote format
    expected_notice = (
        "> This contract describes what an execution adapter claims it is capable of accepting. "
        "It performs no execution, scheduling, planning, orchestration, approval, or dispatch."
    )
    assert expected_notice in md1


def test_deterministic_file_output(tmp_path, sample_dispatch_plan, sample_execution_capability):
    """Verify write_execution_adapter_contract outputs correct path, structure, and byte-identical contents."""
    intent = build_execution_intent(sample_dispatch_plan, "ssh_deployer")
    result = build_execution_adapter_result(
        sample_dispatch_plan, intent, sample_execution_capability
    )

    rel_path1 = write_execution_adapter_contract(result, repo_root=tmp_path)
    expected_rel = f".leira/execution_adapter_contract/{intent.dispatch_id}.{intent.adapter_label}.contract.md"
    assert rel_path1 == expected_rel

    file_path = tmp_path / rel_path1
    assert file_path.exists()
    content1 = file_path.read_bytes()

    # Re-write and ensure identical bytes
    rel_path2 = write_execution_adapter_contract(result, repo_root=tmp_path)
    assert rel_path2 == expected_rel
    content2 = file_path.read_bytes()
    assert content1 == content2


def test_strict_isolation_guardrails(sample_dispatch_plan, sample_execution_capability, tmp_path):
    """Ensure no clocks, UUIDs, randomness, shell calls, file system scanning, or external resources are touched."""
    intent = build_execution_intent(sample_dispatch_plan, "ssh_deployer")
    result = build_execution_adapter_result(
        sample_dispatch_plan, intent, sample_execution_capability
    )

    # Let's mock time, uuid, random, subprocess, os.system, os.walk, and urllib
    with patch("time.time") as mock_time, \
         patch("datetime.datetime") as mock_datetime, \
         patch("uuid.uuid4") as mock_uuid, \
         patch("random.random") as mock_random, \
         patch("subprocess.run") as mock_subproc, \
         patch("os.system") as mock_system, \
         patch("os.walk") as mock_walk:

        md = execution_adapter_contract_markdown(result)
        write_execution_adapter_contract(result, repo_root=tmp_path)

        assert mock_time.call_count == 0
        assert mock_datetime.call_count == 0
        assert mock_uuid.call_count == 0
        assert mock_random.call_count == 0
        assert mock_subproc.call_count == 0
        assert mock_system.call_count == 0
        assert mock_walk.call_count == 0
