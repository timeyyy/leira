"""Leira v4.2 Execution Adapter Contract: deterministic contract between Dispatcher Kernel and execution layers."""

from .contract import (
    ExecutionCapability,
    ExecutionIntent,
    ExecutionAdapterResult,
    build_execution_intent,
    build_execution_adapter_result,
    execution_adapter_contract_markdown,
    write_execution_adapter_contract,
)

__all__ = [
    "ExecutionCapability",
    "ExecutionIntent",
    "ExecutionAdapterResult",
    "build_execution_intent",
    "build_execution_adapter_result",
    "execution_adapter_contract_markdown",
    "write_execution_adapter_contract",
]
