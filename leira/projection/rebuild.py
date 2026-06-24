"""Leira v0.7 projection rebuild: forget the view, keep the truth.

``rebuild_projection()`` reconstructs ``operation_state_projection``
entirely from ``ledger_events``, from nothing -- it never reads the
existing projection table, only overwrites it. The whole rebuild runs
inside a single SQLite transaction: a partially rebuilt projection
would be its own kind of corruption, so a failure partway through
rolls back to the previous (untouched) projection state rather than
leaving a half-written table.

No timestamp is invented here. ``updated_at`` for each run is always
the ``created_at`` of that run's most recent lifecycle event, read
straight from the ledger row.
"""

from __future__ import annotations

import json

from leira.dispatcher.kernel import LedgerKernel
from leira.environment.environment import rebuild_environment_projection
from leira.provenance.git_provenance import rebuild_provenance_projection
from leira.sessions.sessions import rebuild_session_projection
from leira.workspace.workspace import rebuild_artifact_projection

from .state import RUN_LIFECYCLE_EVENT_TYPES, ensure_schema


def rebuild_projection(ledger: LedgerKernel) -> None:
    """Recompute operation_state_projection from ledger_events, in one transaction.

    Reads every ledger event in insertion order and, for each run-level
    lifecycle event, upserts that run's projection row with the event's
    own type/id/created_at. Later events naturally overwrite earlier
    ones, so the row left standing after the full scan reflects the
    most recent event for that run -- identical to how get_run_state()
    derives state directly from the ledger. Idempotent: running this
    twice in a row on an unchanged ledger produces an identical table.
    """
    ensure_schema(ledger)
    conn = ledger.connection

    rows = conn.execute(
        "SELECT id, event_type, payload_json, created_at FROM ledger_events ORDER BY rowid"
    ).fetchall()

    with conn:
        conn.execute("DELETE FROM operation_state_projection")
        for event_id, event_type, payload_json, created_at in rows:
            if event_type not in RUN_LIFECYCLE_EVENT_TYPES:
                continue
            payload = json.loads(payload_json)
            run_id = payload.get("run_id")
            if not isinstance(run_id, str):
                continue
            conn.execute(
                """
                INSERT INTO operation_state_projection
                    (run_id, current_state, last_event_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    current_state = excluded.current_state,
                    last_event_id = excluded.last_event_id,
                    updated_at = excluded.updated_at
                """,
                (run_id, event_type, event_id, created_at),
            )

    rebuild_artifact_projection(ledger)
    rebuild_provenance_projection(ledger)
    rebuild_environment_projection(ledger)
    rebuild_session_projection(ledger)
