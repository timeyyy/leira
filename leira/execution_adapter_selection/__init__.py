"""Leira v4.4 Execution Adapter Selection Kernel API."""

from .selection import (
    AdapterSelection,
    AdapterSelectionResult,
    select_execution_adapters,
    build_adapter_selection_result,
    adapter_selection_markdown,
    write_adapter_selection,
)

__all__ = [
    "AdapterSelection",
    "AdapterSelectionResult",
    "select_execution_adapters",
    "build_adapter_selection_result",
    "adapter_selection_markdown",
    "write_adapter_selection",
]
