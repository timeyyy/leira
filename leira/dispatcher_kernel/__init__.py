"""Leira v4.0 dispatcher kernel: deterministic derivation of execution plans."""

from .dispatcher import (
    DispatchPlan,
    DispatcherKernelResult,
    build_dispatch_plan,
    run_dispatcher_kernel,
    dispatcher_kernel_markdown,
    write_dispatcher_kernel,
    DispatchPlanReceipt,
    build_dispatch_plan_receipt,
    dispatch_plan_receipt_json,
    write_dispatcher_kernel_receipt,
)

__all__ = [
    "DispatchPlan",
    "DispatcherKernelResult",
    "build_dispatch_plan",
    "run_dispatcher_kernel",
    "dispatcher_kernel_markdown",
    "write_dispatcher_kernel",
    "DispatchPlanReceipt",
    "build_dispatch_plan_receipt",
    "dispatch_plan_receipt_json",
    "write_dispatcher_kernel_receipt",
]
