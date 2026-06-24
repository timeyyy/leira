"""Leira v0 kernel: a minimal hash-chained, append-only local event ledger.

What this is
-------------
This is *not* an agent system, *not* an orchestrator, and *not* a worker
runtime. It is the smallest honest thing under all of that: a single-process,
single-writer, SQLite-backed ledger of events, chained by SHA-256 so that
each event cryptographically references the event before it.

The point of v0 is narrow: the machine must be able to say no. It can
refuse a malformed payload (``append_event`` returns a typed failure
instead of silently coercing data), and it can detect when the ledger it
is reading has been altered outside of its own append path
(``validate_chain`` recomputes every hash and refuses to repair anything).

Security scope (read this before trusting the chain for anything)
-------------------------------------------------------------------
The hash chain protects against:
  - accidental mutation (a stray UPDATE, a buggy migration)
  - silent corruption (bit rot, a half-written row, a bad merge of two
    ledger files)
  - replay mismatch (an event inserted out of order, or with a parent
    hash that doesn't match the true predecessor)

The hash chain does NOT protect against:
  - a malicious local actor with write access to the SQLite file, who can
    rewrite the entire chain (including every hash) consistently
  - a compromised kernel process, which could simply not validate, or
    could compute hashes over whatever it wants
  - filesystem-level theft or tampering performed with full knowledge of
    the hashing scheme
  - multiple concurrent writers (v0 is single-process, single-writer;
    there is no optimistic concurrency control, no locking protocol)

In short: this is a tamper-*evidence* mechanism for one trusted process
talking to its own database, not a tamper-*proof* security boundary
against an adversary who can run code or edit files on the same machine.

Explicitly out of scope for v0
-------------------------------
Projections, rebuild_projection, snapshots, workers, adapters, quotas,
a conductor loop, routing, MCP, any LLM provider integration,
multi-process access, a network service, dashboards, a claim registry,
belief_promoted events, convergence receipts, operation contracts. None
of that exists here, on purpose.
"""

from __future__ import annotations

import json
import math
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Fixed genesis parent hash referenced by the first event in any ledger.
GENESIS_PARENT_HASH = (
    "0000000000000000000000000000000000000000000000000000000000000000"
)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class PayloadValidationError(ValueError):
    """Raised internally when a payload fails canonicalization rules."""


@dataclass(frozen=True)
class AppendResult:
    success: bool
    event_id: str | None = None
    event_hash: str | None = None
    error_type: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class ValidateChainResult:
    success: bool
    events_checked: int = 0
    failed_event_id: str | None = None
    error_type: str | None = None
    message: str | None = None


def _normalize_value(value):
    """Recursively NFC-normalize strings and reject non-canonical content.

    Rejects floats outright (this also rejects NaN/Infinity, since those
    are float instances in Python) and rejects non-string dict keys.
    """
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        # Covers ordinary floats as well as NaN / Infinity / -Infinity.
        raise PayloadValidationError(
            f"floats are not allowed in payloads: {value!r}"
        )
    if isinstance(value, dict):
        normalized = {}
        for key, val in value.items():
            if not isinstance(key, str):
                raise PayloadValidationError(
                    f"dict keys must be strings, got {type(key).__name__}: {key!r}"
                )
            normalized[unicodedata.normalize("NFC", key)] = _normalize_value(val)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    raise PayloadValidationError(
        f"unsupported payload value type: {type(value).__name__}"
    )


def canonicalize_payload(payload: dict) -> str:
    """Produce the deterministic JSON string used for both storage and hashing.

    Raises PayloadValidationError if the payload contains floats, NaN,
    Infinity, or non-string dict keys.
    """
    if not isinstance(payload, dict):
        raise PayloadValidationError("payload must be a dict")
    normalized = _normalize_value(payload)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def compute_event_hash(
    *,
    parent_event_hash: str,
    event_type: str,
    worker_id: str,
    artifact_hash: str | None,
    payload_json: str,
    created_at: str,
) -> str:
    """SHA-256 over the canonicalized event content.

    Plain hashing, no salt, no HMAC: this is tamper-evidence within a
    single trusted process, not a security boundary (see module docstring).
    """
    import hashlib

    hash_input = {
        "parent_event_hash": unicodedata.normalize("NFC", parent_event_hash),
        "event_type": unicodedata.normalize("NFC", event_type),
        "worker_id": unicodedata.normalize("NFC", worker_id),
        "artifact_hash": (
            unicodedata.normalize("NFC", artifact_hash)
            if artifact_hash is not None
            else None
        ),
        "payload_json": payload_json,
        "created_at": unicodedata.normalize("NFC", created_at),
    }
    canonical = json.dumps(
        hash_input,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LedgerKernel:
    """A single-process, single-writer, append-only hash-chained ledger."""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        schema = _SCHEMA_PATH.read_text(encoding="utf-8")
        self._conn.executescript(schema)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LedgerKernel":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _head_hash(self) -> str:
        # rowid reflects true insertion order; created_at is metadata only
        # and must not be relied on for ordering (clocks can collide).
        row = self._conn.execute(
            "SELECT event_hash FROM ledger_events ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS_PARENT_HASH

    def append_event(
        self,
        *,
        event_type: str,
        worker_id: str,
        payload: dict,
        artifact_hash: str | None = None,
        operation_id: str | None = None,
    ) -> AppendResult:
        """Append one event to the ledger.

        Loads the current head hash (or genesis if the ledger is empty),
        canonicalizes and validates the payload, computes the event hash,
        and inserts the row in a single transaction. Never raises for
        ordinary validation failures; returns a typed AppendResult instead.
        """
        if not isinstance(event_type, str) or not event_type:
            return AppendResult(
                success=False,
                error_type="INVALID_EVENT_TYPE",
                message="event_type must be a non-empty string",
            )
        if not isinstance(worker_id, str) or not worker_id:
            return AppendResult(
                success=False,
                error_type="INVALID_WORKER_ID",
                message="worker_id must be a non-empty string",
            )

        try:
            payload_json = canonicalize_payload(payload)
        except PayloadValidationError as exc:
            return AppendResult(
                success=False,
                error_type="INVALID_PAYLOAD",
                message=str(exc),
            )

        created_at = datetime.now(timezone.utc).isoformat()
        event_id = str(uuid.uuid4())

        try:
            with self._conn:
                parent_event_hash = self._head_hash()
                event_hash = compute_event_hash(
                    parent_event_hash=parent_event_hash,
                    event_type=event_type,
                    worker_id=worker_id,
                    artifact_hash=artifact_hash,
                    payload_json=payload_json,
                    created_at=created_at,
                )
                self._conn.execute(
                    """
                    INSERT INTO ledger_events (
                        id, operation_id, parent_event_hash, event_type,
                        worker_id, payload_json, artifact_hash, event_hash,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        operation_id,
                        parent_event_hash,
                        event_type,
                        worker_id,
                        payload_json,
                        artifact_hash,
                        event_hash,
                        created_at,
                    ),
                )
        except sqlite3.Error as exc:
            return AppendResult(
                success=False,
                error_type="DB_ERROR",
                message=str(exc),
            )

        return AppendResult(
            success=True,
            event_id=event_id,
            event_hash=event_hash,
        )

    def validate_chain(self) -> ValidateChainResult:
        """Recompute every event hash and verify the chain links correctly.

        Reads events in insertion order, recomputes each event_hash from
        its stored fields, and verifies that each parent_event_hash
        matches the previous row's event_hash (genesis for the first row).
        Never repairs anything; only reports where the chain broke.
        """
        rows = self._conn.execute(
            """
            SELECT id, parent_event_hash, event_type, worker_id,
                   payload_json, artifact_hash, event_hash, created_at
            FROM ledger_events
            ORDER BY rowid
            """
        ).fetchall()

        expected_parent_hash = GENESIS_PARENT_HASH
        for idx, row in enumerate(rows):
            (
                event_id,
                parent_event_hash,
                event_type,
                worker_id,
                payload_json,
                artifact_hash,
                event_hash,
                created_at,
            ) = row

            if parent_event_hash != expected_parent_hash:
                return ValidateChainResult(
                    success=False,
                    events_checked=idx,
                    failed_event_id=event_id,
                    error_type="CHAIN_BROKEN",
                    message=(
                        f"event {event_id} has parent_event_hash "
                        f"{parent_event_hash!r}, expected "
                        f"{expected_parent_hash!r}"
                    ),
                )

            recomputed = compute_event_hash(
                parent_event_hash=parent_event_hash,
                event_type=event_type,
                worker_id=worker_id,
                artifact_hash=artifact_hash,
                payload_json=payload_json,
                created_at=created_at,
            )
            if recomputed != event_hash:
                return ValidateChainResult(
                    success=False,
                    events_checked=idx,
                    failed_event_id=event_id,
                    error_type="HASH_MISMATCH",
                    message=(
                        f"event {event_id} stored event_hash {event_hash!r} "
                        f"does not match recomputed hash {recomputed!r}"
                    ),
                )

            expected_parent_hash = event_hash

        return ValidateChainResult(success=True, events_checked=len(rows))
