"""Path handling for the local evidence workspace."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path, PureWindowsPath

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class WorkspaceError(ValueError):
    """Typed workspace rejection."""

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type


def normalize_relative_path(relative_path: str) -> str:
    """Normalize and validate an untrusted artifact relative path."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise WorkspaceError("INVALID_PATH", "relative_path must be a non-empty string")
    if relative_path.startswith("/") or Path(relative_path).is_absolute():
        raise WorkspaceError("INVALID_PATH", "absolute artifact paths are not allowed")
    if PureWindowsPath(relative_path).is_absolute() or "\\" in relative_path or ":" in relative_path:
        raise WorkspaceError("INVALID_PATH", "platform-specific artifact paths are not allowed")

    normalized = posixpath.normpath(relative_path)
    if normalized in ("", "."):
        raise WorkspaceError("INVALID_PATH", "relative_path must name a file")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise WorkspaceError("PATH_TRAVERSAL", "artifact paths may not traverse directories")
    if any(not _SEGMENT_RE.fullmatch(part) for part in parts):
        raise WorkspaceError("INVALID_PATH", "artifact paths use a restricted filename alphabet")
    return normalized


def get_artifacts_dir(workspace_root: Path, intent_id: str) -> Path:
    return workspace_root / "intents" / intent_id / "artifacts"


def _get_artifact_path(workspace_root: Path, intent_id: str, relative_path: str) -> Path:
    """Return the absolute path for a validated artifact location."""
    normalized = normalize_relative_path(relative_path)
    base = get_artifacts_dir(workspace_root, intent_id).resolve(strict=False)
    path = (base / normalized).resolve(strict=False)
    if base != path and base not in path.parents:
        raise WorkspaceError("PATH_TRAVERSAL", "artifact path escaped the artifacts directory")
    return path
