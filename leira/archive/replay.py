"""Replay ledger history into disposable projections, without executing work."""

from __future__ import annotations

from pathlib import Path
import json

from leira.audit.auditor import AuditResult, audit
from leira.claims.claims import rebuild_claim_projection
from leira.dispatcher.kernel import LedgerKernel
from leira.environment.environment import rebuild_environment_projection
from leira.inbox.inbox import ensure_schema as ensure_inbox_schema
from leira.inbox.inbox import rebuild_intent_projection
from leira.projection.rebuild import rebuild_projection
from leira.provenance.git_provenance import rebuild_provenance_projection
from leira.receipts.receipts import rebuild_receipt_projection
from leira.registry.registry import rebuild_worker_projection
from leira.sessions.sessions import rebuild_session_projection
from leira.workspace.workspace import rebuild_artifact_projection


def rebuild_inbox_entries(ledger: LedgerKernel) -> None:
    """Reconstruct durable ingress rows from intent submission/rejection events."""
    ensure_inbox_schema(ledger)
    rows = ledger.connection.execute(
        """
        SELECT id, event_type, payload_json, created_at
        FROM ledger_events
        WHERE event_type IN ('intent_submitted', 'intent_rejected')
        ORDER BY rowid
        """
    ).fetchall()
    with ledger.connection:
        ledger.connection.execute("DELETE FROM inbox_entries")
        for _event_id, _event_type, payload_json, created_at in rows:
            try:
                payload = json.loads(payload_json)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            intent_id = payload.get("intent_id")
            intent_type = payload.get("intent_type")
            stored_payload = payload.get("payload")
            status = payload.get("status")
            if (
                not isinstance(intent_id, str)
                or not intent_id
                or not isinstance(intent_type, str)
                or not isinstance(stored_payload, dict)
                or not isinstance(status, str)
            ):
                continue
            ledger.connection.execute(
                """
                INSERT OR IGNORE INTO inbox_entries
                    (intent_id, created_at, intent_type, payload_json, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    intent_id,
                    created_at,
                    intent_type,
                    json.dumps(
                        stored_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                    status,
                ),
            )


def replay_history(
    ledger: LedgerKernel, workspace_root: str | Path | None = None
) -> AuditResult:
    """Rebuild all projections from ledger_events, then audit.

    This function deliberately does not import dispatcher, worker,
    shell, or Git execution helpers. It only rebuilds derived SQLite
    tables from already-imported ledger history and optionally verifies
    workspace files through the auditor.
    """
    rebuild_inbox_entries(ledger)
    rebuild_projection(ledger)
    rebuild_intent_projection(ledger)
    rebuild_worker_projection(ledger)
    rebuild_claim_projection(ledger)
    rebuild_receipt_projection(ledger)
    rebuild_artifact_projection(ledger)
    rebuild_provenance_projection(ledger)
    rebuild_environment_projection(ledger)
    rebuild_session_projection(ledger)
    return audit(ledger, workspace_root)
