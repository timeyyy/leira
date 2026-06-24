"""Leira v1.3 receipt bundles: organized evidence, not a story.

What this is
-------------
A receipt bundle is every ledger event associated with one intent_id,
exposed together, in ledger order, exactly as recorded. It does not
summarize what happened, explain why, score an outcome, or decide
whether an intent "succeeded" -- it is a view over the ledger, nothing
more. The ledger remains the only source of truth; a receipt bundle
can always be rebuilt and never needs to be trusted over it.

Finding every event for an intent_id
----------------------------------------
Most intent-scoped events (``intent_submitted``, ``intent_rejected``,
``intent_claimed``/``intent_completed``/``intent_failed`` from
``leira.dispatcher.dispatcher``, and ``intent_claim_established``/
``intent_released``/``intent_claim_rejected``/``intent_release_rejected``
from ``leira.claims.claims``) carry ``intent_id`` directly in their
payload, and are found with a direct, single query.

``state_running``/``artifact_written`` (from the unmodified
``leira.dispatcher.lifecycle.LifecycleKernel``) do not -- they only
carry ``run_id``. The bridge is ``run_created``, which
``leira.dispatcher.dispatcher.dispatch_once`` always creates with
``operation_id=intent_id`` (a real ``ledger_events`` column, not a
payload search) and whose own payload carries the ``run_id`` it just
minted. ``list_receipt_events`` therefore runs three read-only
queries -- direct intent_id matches, ``run_created`` rows for this
``operation_id``, and every event referencing the ``run_id`` such a
``run_created`` row produced -- and merges the results, deduplicated
by event id. None of this required changing the ledger, lifecycle,
inbox, dispatcher, registry, or claim store: every query goes through
the existing, public ``LedgerKernel.connection`` escape hatch, exactly
as ``leira.inbox.inbox.get_intent_status`` and
``leira.claims.claims.get_claim`` already do.

Chronology rule
-----------------
Events are always ordered by ``rowid`` -- true ledger insertion order
-- never by ``created_at``. Timestamps may collide; ledger order
cannot. This applies to ``list_receipt_events``, ``rebuild``, and the
auditor's own expected-bundle computation alike.

Receipt projection vs. the bundle itself
--------------------------------------------
``receipt_projection`` (``intent_id``, ``first_event_id``,
``last_event_id``, ``event_count``, ``updated_at``) is a disposable
cache of a bundle's *shape*, never its source: ``list_receipt_events``
always queries ``ledger_events`` directly, never
``receipt_projection``. The projection exists purely so the auditor
(and any other caller) has something cheap to check the ledger
against; losing it loses nothing -- ``rebuild_receipt_projection()``
recomputes it completely.

What this explicitly does NOT do
-----------------------------------
No summarizing, interpreting, explaining, compressing, scoring,
reordering, judging worker outputs, or deciding success. No
signatures, no external verification. A receipt bundle is exposure,
not analysis.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass

from leira.dispatcher.kernel import LedgerEvent, LedgerKernel

_COLUMNS = (
    "rowid, id, operation_id, parent_event_hash, event_type, "
    "worker_id, payload_json, artifact_hash, event_hash, created_at"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipt_projection (
    intent_id TEXT PRIMARY KEY,
    first_event_id TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def ensure_schema(ledger: LedgerKernel) -> None:
    ledger.connection.executescript(_SCHEMA)
    ledger.connection.commit()


@dataclass(frozen=True)
class ReceiptBundle:
    intent_id: str
    first_event_id: str
    last_event_id: str
    event_count: int
    events: list[LedgerEvent]


def _row_to_event(row: tuple) -> LedgerEvent:
    # row[0] is rowid, used only for ordering -- not part of LedgerEvent.
    return LedgerEvent(
        id=row[1],
        operation_id=row[2],
        parent_event_hash=row[3],
        event_type=row[4],
        worker_id=row[5],
        payload_json=row[6],
        artifact_hash=row[7],
        event_hash=row[8],
        created_at=row[9],
    )


def list_receipt_events(ledger: LedgerKernel, intent_id: str) -> list[LedgerEvent]:
    """Every ledger event for intent_id, in true ledger order. Reads ledger_events only.

    The projection cache further down this module is always derived
    from this function's output, never the other way around.
    """
    conn = ledger.connection
    rows_by_event_id: dict[str, tuple] = {}

    def _collect(rows):
        for row in rows:
            rows_by_event_id[row[1]] = row

    direct_rows = conn.execute(
        f"SELECT {_COLUMNS} FROM ledger_events WHERE payload_json LIKE ? ORDER BY rowid",
        (f'%"intent_id":"{intent_id}"%',),
    ).fetchall()
    _collect(direct_rows)

    run_created_rows = conn.execute(
        f"SELECT {_COLUMNS} FROM ledger_events "
        "WHERE event_type = 'run_created' AND operation_id = ? ORDER BY rowid",
        (intent_id,),
    ).fetchall()
    _collect(run_created_rows)

    run_ids: set[str] = set()
    for row in run_created_rows:
        try:
            payload = json.loads(row[6])
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            run_id = payload.get("run_id")
            if isinstance(run_id, str) and run_id:
                run_ids.add(run_id)

    for run_id in run_ids:
        run_rows = conn.execute(
            f"SELECT {_COLUMNS} FROM ledger_events WHERE payload_json LIKE ? ORDER BY rowid",
            (f'%"run_id":"{run_id}"%',),
        ).fetchall()
        _collect(run_rows)

    ordered_rows = sorted(rows_by_event_id.values(), key=lambda row: row[0])
    return [_row_to_event(row) for row in ordered_rows]


def _update_receipt_projection(
    ledger: LedgerKernel,
    *,
    intent_id: str,
    first_event_id: str,
    last_event_id: str,
    event_count: int,
    updated_at: str,
) -> bool:
    """Best-effort live upsert of one receipt_projection row. Never raises."""
    try:
        ensure_schema(ledger)
        with ledger.connection:
            ledger.connection.execute(
                """
                INSERT INTO receipt_projection
                    (intent_id, first_event_id, last_event_id, event_count, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    first_event_id = excluded.first_event_id,
                    last_event_id = excluded.last_event_id,
                    event_count = excluded.event_count,
                    updated_at = excluded.updated_at
                """,
                (intent_id, first_event_id, last_event_id, event_count, updated_at),
            )
        return True
    except sqlite3.Error:
        return False


def get_receipt_bundle(ledger: LedgerKernel, intent_id: str) -> ReceiptBundle | None:
    """Build intent_id's receipt bundle straight from the ledger, or None if it has no events.

    A bundle exists for an in-progress intent exactly as for a
    completed, failed, or rejected one -- there is no terminal-state
    requirement here. After building the bundle, best-effort refreshes
    receipt_projection (never the other way around).
    """
    events = list_receipt_events(ledger, intent_id)
    if not events:
        return None

    bundle = ReceiptBundle(
        intent_id=intent_id,
        first_event_id=events[0].id,
        last_event_id=events[-1].id,
        event_count=len(events),
        events=events,
    )
    _update_receipt_projection(
        ledger,
        intent_id=intent_id,
        first_event_id=bundle.first_event_id,
        last_event_id=bundle.last_event_id,
        event_count=bundle.event_count,
        updated_at=events[-1].created_at,
    )
    return bundle


def export_receipt_bundle(ledger: LedgerKernel, intent_id: str) -> dict:
    """Plain-dict, JSON-ready export of intent_id's receipt bundle.

    Exporting the same bundle twice and serializing both with
    json.dumps(..., sort_keys=True, separators=(",", ":")) must
    produce byte-identical output -- every value here is already a
    plain str/int/bool/None/list/dict, and LedgerEvent's own field
    order does not matter once sort_keys=True is applied.
    """
    bundle = get_receipt_bundle(ledger, intent_id)
    if bundle is None:
        return {
            "intent_id": intent_id,
            "found": False,
            "first_event_id": None,
            "last_event_id": None,
            "event_count": 0,
            "events": [],
        }
    return {
        "intent_id": bundle.intent_id,
        "found": True,
        "first_event_id": bundle.first_event_id,
        "last_event_id": bundle.last_event_id,
        "event_count": bundle.event_count,
        "events": [asdict(event) for event in bundle.events],
    }


def rebuild_receipt_projection(ledger: LedgerKernel) -> None:
    """Recompute receipt_projection entirely from ledger_events, in one transaction.

    Enumerates every intent_id known to the ledger (from
    intent_submitted/intent_rejected -- the same "what intents exist"
    source leira.inbox.inbox itself is built on), then reuses
    list_receipt_events() for each one, so a bundle's rebuilt shape can
    never silently diverge from what get_receipt_bundle() would
    compute live. All-or-nothing: a failure partway through rolls back
    to the previous table state.
    """
    ensure_schema(ledger)
    conn = ledger.connection

    rows = conn.execute(
        "SELECT payload_json FROM ledger_events "
        "WHERE event_type IN ('intent_submitted', 'intent_rejected') ORDER BY rowid"
    ).fetchall()

    intent_ids: list[str] = []
    seen: set[str] = set()
    for (payload_json,) in rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        intent_id = payload.get("intent_id")
        if isinstance(intent_id, str) and intent_id and intent_id not in seen:
            seen.add(intent_id)
            intent_ids.append(intent_id)

    with conn:
        conn.execute("DELETE FROM receipt_projection")
        for intent_id in intent_ids:
            events = list_receipt_events(ledger, intent_id)
            if not events:
                continue
            conn.execute(
                """
                INSERT INTO receipt_projection
                    (intent_id, first_event_id, last_event_id, event_count, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (intent_id, events[0].id, events[-1].id, len(events), events[-1].created_at),
            )
