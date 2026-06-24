"""Leira v0.7 projection: a disposable view, not a second truth.

What this is
-------------
``operation_state_projection`` is a single small table -- one row per
run, holding only ``current_state``, ``last_event_id``, and
``updated_at`` -- kept as a convenience cache over the ledger. Every
field in it is derivable from ``ledger_events``; nothing here is
inferred, and nothing here is authoritative.

History is truth. Projections are convenience. If this table is lost,
corrupted, or simply deleted, nothing important is lost: rebuild.py's
``rebuild_projection()`` can always recompute it exactly from the
ledger. The kernel never trusts this table over the ledger.

``updated_at`` is always copied from the ledger event's own
``created_at`` -- never generated here, never ``datetime.now()``.
"""

from __future__ import annotations

import sqlite3

from leira.dispatcher.kernel import LedgerKernel

# The only lifecycle event types a projection row can reflect. Anything
# else (e.g. operation_validated) is operation-level, not run-level,
# and is intentionally invisible to this projection.
RUN_LIFECYCLE_EVENT_TYPES = frozenset(
    {"run_created", "state_running", "artifact_written", "state_completed"}
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operation_state_projection (
    run_id TEXT PRIMARY KEY,
    current_state TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def ensure_schema(ledger: LedgerKernel) -> None:
    ledger.connection.execute(_SCHEMA)
    ledger.connection.commit()


class ProjectionEngine:
    """Read/write access to operation_state_projection for one ledger."""

    def __init__(self, ledger: LedgerKernel):
        self._ledger = ledger
        ensure_schema(self._ledger)

    def get_current_state(self, run_id: str) -> str | None:
        """Return the projected current_state for run_id, or None.

        A run with no projection row (never seen, or never rebuilt
        after deletion) returns None -- this is not an error. Always
        queries the table directly; nothing is cached in this object.
        """
        row = self._ledger.connection.execute(
            "SELECT current_state FROM operation_state_projection WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return row[0] if row else None

    def update_from_event(
        self, *, run_id: str, current_state: str, last_event_id: str, updated_at: str
    ) -> bool:
        """Upsert one projection row. Never raises -- returns False on failure.

        Projection writes are convenience; a failure here must never
        invalidate an already-successful ledger append. A False return
        means "rebuild_projection() will fix this eventually," not an
        error the caller needs to propagate.
        """
        try:
            with self._ledger.connection:
                self._ledger.connection.execute(
                    """
                    INSERT INTO operation_state_projection
                        (run_id, current_state, last_event_id, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        current_state = excluded.current_state,
                        last_event_id = excluded.last_event_id,
                        updated_at = excluded.updated_at
                    """,
                    (run_id, current_state, last_event_id, updated_at),
                )
            return True
        except sqlite3.Error:
            return False
