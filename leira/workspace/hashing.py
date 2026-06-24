"""Hashing helpers for file-backed artifacts."""

from __future__ import annotations

import hashlib


def sha256(content: bytes) -> str:
    """Return the SHA-256 hex digest for exactly these bytes."""
    return hashlib.sha256(content).hexdigest()
