"""Leira v0.1 operation envelope: the smallest gate on starting work.

What this is
-------------
An operation envelope is a small YAML document that must exist and must
have the right *shape* before any operation is allowed to run. This
module does not run operations, does not interpret what they mean, and
does not judge whether they are reasonable. It only answers one
question: does this envelope have the required fields, with the
required types?

No envelope. No run. That's the whole feature.

What this explicitly does NOT do
-----------------------------------
The kernel never evaluates whether objectives are reasonable, whether
claims are true, whether assumptions are correct, or whether failure
descriptions are meaningful. ``metadata`` (assumptions, claims,
failure_distinguishability, or anything else placed there) is carried
through unexamined — it is opaque payload, not something this module
parses or validates beyond "the YAML loaded as a mapping."

This module does not start, run, dispatch, route, or otherwise act on
an operation. It only loads and structurally validates the envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ContractResult:
    success: bool
    error_type: str | None = None
    message: str | None = None
    operation_id: str | None = None
    operation: dict | None = None


def load_operation(path: str | Path) -> ContractResult:
    """Read a YAML file from disk and parse it with yaml.safe_load.

    Never raises for ordinary failure modes (missing file, malformed
    YAML, non-mapping document) — returns a ContractResult instead.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return ContractResult(
            success=False,
            error_type="FILE_NOT_FOUND",
            message=f"could not read {path!r}: {exc}",
        )

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return ContractResult(
            success=False,
            error_type="MALFORMED_YAML",
            message=f"{path!r} is not valid YAML: {exc}",
        )

    if not isinstance(document, dict):
        return ContractResult(
            success=False,
            error_type="MALFORMED_YAML",
            message=f"{path!r} must parse to a YAML mapping, got "
            f"{type(document).__name__}",
        )

    return ContractResult(success=True, operation=document)


def validate_operation(document: dict) -> ContractResult:
    """Structurally validate a parsed envelope document.

    Enforces only: presence of required fields, type consistency, and
    YAML structure under ``operation``. Never evaluates meaning —
    ``metadata`` is not inspected at all beyond already having been
    parsed as part of the document.
    """
    if not isinstance(document, dict):
        return ContractResult(
            success=False,
            error_type="MALFORMED_ENVELOPE",
            message=f"envelope must be a mapping, got {type(document).__name__}",
        )

    operation = document.get("operation")
    if not isinstance(operation, dict):
        return ContractResult(
            success=False,
            error_type="MISSING_OPERATION",
            message="envelope must contain an 'operation' mapping",
        )

    operation_id = operation.get("id")
    if operation_id is None:
        return ContractResult(
            success=False,
            error_type="MISSING_ID",
            message="operation.id is required",
        )
    if not isinstance(operation_id, str):
        return ContractResult(
            success=False,
            error_type="INVALID_ID",
            message="operation.id must be a string",
        )

    objective = operation.get("objective")
    if objective is None:
        return ContractResult(
            success=False,
            error_type="MISSING_OBJECTIVE",
            message="operation.objective is required",
            operation_id=operation_id,
        )
    if not isinstance(objective, str):
        return ContractResult(
            success=False,
            error_type="INVALID_OBJECTIVE",
            message="operation.objective must be a string",
            operation_id=operation_id,
        )

    success_criteria = operation.get("success_criteria")
    if success_criteria is None:
        return ContractResult(
            success=False,
            error_type="MISSING_SUCCESS_CRITERIA",
            message="operation.success_criteria is required",
            operation_id=operation_id,
        )
    if not isinstance(success_criteria, list):
        return ContractResult(
            success=False,
            error_type="INVALID_SUCCESS_CRITERIA",
            message="operation.success_criteria must be a list",
            operation_id=operation_id,
        )
    if not all(isinstance(item, str) for item in success_criteria):
        return ContractResult(
            success=False,
            error_type="INVALID_SUCCESS_CRITERIA",
            message="operation.success_criteria must be a list of strings",
            operation_id=operation_id,
        )

    return ContractResult(
        success=True,
        operation_id=operation_id,
        operation=document,
    )


def load_and_validate(path: str | Path) -> ContractResult:
    """Load an envelope from disk and structurally validate it.

    Convenience wrapper: ``load_operation`` then ``validate_operation``.
    """
    loaded = load_operation(path)
    if not loaded.success:
        return loaded
    return validate_operation(loaded.operation)
