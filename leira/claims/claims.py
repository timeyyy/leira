"""Leira v1.2 claim store: exclusive ownership, not scheduling.

What this is
-------------
``claim_intent(intent_id, owner_id) -> ClaimResult`` and
``release_claim(intent_id, owner_id) -> ReleaseResult`` establish and
release exclusive ownership of an intent. Ownership is a lock, not a
plan: claiming an intent does not run it, choose a worker for it, or
change its PENDING/RUNNING/COMPLETED/FAILED status (that state machine
belongs entirely to ``leira.inbox.inbox`` and
``leira.dispatcher.dispatcher`` -- unmodified, unredesigned). The
claim store only answers "who, if anyone, currently owns this intent,"
and refuses a second owner while the first one holds it.

``owner_id`` is an opaque string -- "dispatcher-1", "worker-a",
"process-17" -- whatever the caller wants. The claim store never
interprets it, never checks liveness, never assumes a process behind
it is still running.

A deliberate naming deviation
------------------------------
The v1.0 dispatcher already owns the ledger event type
``"intent_claimed"`` (see ``leira.inbox.inbox.INTENT_LEDGER_EVENT_TYPES``
and ``leira.dispatcher.dispatcher.dispatch_once``), with a different
payload shape (``status``, ``worker_name``) feeding a different state
machine (``ALLOWED_INTENT_TRANSITIONS``). Reusing that exact literal
event type here for a second, unrelated meaning would corrupt
``leira.inbox.inbox.rebuild_intent_projection`` and every
``leira.audit.auditor`` check keyed on it
(``check_duplicate_claims``, ``check_intent_transitions``,
``compute_expected_intents``) -- exactly the modules this version is
told not to redesign. This module therefore appends
``"intent_claim_established"`` for a successful claim instead of the
literally-specified ``"intent_claimed"``; every other event name
(``intent_released``, ``intent_claim_rejected``,
``intent_release_rejected``) matches the spec exactly, since none of
those collide with anything v1.0 already owns. The two ownership
mechanisms are intentionally orthogonal and layered: an intent can be
both "PENDING" (inbox's status) and actively claimed (this module's
lock) at the same time -- claiming does not advance inbox status, and
inbox status does not know claims exist.

Claim-ledger atomicity
------------------------
The same ordering discipline as every other write in this system:
validate, append the ledger event, only then -- for a success --
update the live projection (best-effort; ``rebuild_claim_projection``
can always recompute it). A failed ledger append never produces a
recorded claim or release.

Failure model: orphans are honest, not bugs
----------------------------------------------
There are no leases, no expiration, no liveness checks, no automatic
recovery. A crashed owner leaves its claim active forever; that claim
remains fully visible via ``get_claim()`` and in
``intent_claim_projection``, and the auditor reports it as exactly
what it is -- an active claim -- never as a problem to silently fix.
If a release fails after a successful dispatch, the claim stays
active; nothing here retries or pretends otherwise.

What this explicitly does NOT do
-----------------------------------
No scheduling, no parallelism, no load balancing, no worker pools, no
leases, no claim expiration, no claim stealing, no automatic recovery,
no orphan cleanup, no liveness checks, no retries.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import get_intent_status

CLAIMS_WORKER_ID = "kernel"

# See "A deliberate naming deviation" above: this is the one event
# name in this module that differs from the literal v1.2 spec text,
# to avoid colliding with v1.0's own, unrelated "intent_claimed".
CLAIM_ESTABLISHED_EVENT_TYPE = "intent_claim_established"
CLAIM_RELEASED_EVENT_TYPE = "intent_released"
CLAIM_REJECTED_EVENT_TYPE = "intent_claim_rejected"
RELEASE_REJECTED_EVENT_TYPE = "intent_release_rejected"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intent_claim_projection (
    intent_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL
);
"""


def ensure_schema(ledger: LedgerKernel) -> None:
    ledger.connection.executescript(_SCHEMA)
    ledger.connection.commit()


@dataclass(frozen=True)
class ClaimInfo:
    intent_id: str
    claim_id: str
    owner_id: str
    claimed_at: str
    last_event_id: str


@dataclass(frozen=True)
class ClaimResult:
    success: bool
    claim_id: str | None
    error_type: str | None = None


@dataclass(frozen=True)
class ReleaseResult:
    success: bool
    error_type: str | None = None


def _claim_artifact(claim_id: str | None, intent_id: str, owner_id, action: str, error_type: str | None) -> dict:
    return {
        "type": "claim",
        "content": {
            "claim_id": claim_id,
            "intent_id": intent_id,
            "owner_id": owner_id,
            "action": action,
            "error_type": error_type,
        },
    }


def replay_claim_events(
    intent_events: list[tuple[str, dict, str, str]],
) -> ClaimInfo | None:
    """Replay one intent_id's claim/release events; return the final active claim, if any.

    ``intent_events`` must already be filtered to a single intent_id
    and ordered chronologically (ledger insertion order), as a list of
    ``(event_type, payload, created_at, event_id)`` tuples. Shared by
    ``get_claim()``, ``rebuild_claim_projection()``, and
    ``leira.audit.auditor.compute_expected_claim_projection()`` so the
    replay rule is defined exactly once.

    A second CLAIM_ESTABLISHED_EVENT_TYPE while one is already active
    is illegal history -- the first claim wins, the second is ignored
    here (reported elsewhere as DUPLICATE_ACTIVE_CLAIM, never silently
    treated as an update). A release only clears the active claim if
    its claim_id and owner_id both match; a mismatched release is
    likewise ignored here (reported as RELEASE_OWNER_MISMATCH).
    """
    active: ClaimInfo | None = None
    for event_type, payload, created_at, event_id in intent_events:
        if event_type == CLAIM_ESTABLISHED_EVENT_TYPE:
            if active is not None:
                continue
            claim_id = payload.get("claim_id")
            owner_id = payload.get("owner_id")
            intent_id = payload.get("intent_id")
            if not isinstance(claim_id, str) or not claim_id:
                continue
            if not isinstance(owner_id, str) or not owner_id:
                continue
            if not isinstance(intent_id, str) or not intent_id:
                continue
            active = ClaimInfo(
                intent_id=intent_id,
                claim_id=claim_id,
                owner_id=owner_id,
                claimed_at=created_at,
                last_event_id=event_id,
            )
        elif event_type == CLAIM_RELEASED_EVENT_TYPE:
            if active is None:
                continue
            claim_id = payload.get("claim_id")
            owner_id = payload.get("owner_id")
            if claim_id == active.claim_id and owner_id == active.owner_id:
                active = None
    return active


def get_claim(ledger: LedgerKernel, intent_id: str) -> ClaimInfo | None:
    """Derive intent_id's current active claim straight from the ledger.

    Mirrors leira.inbox.inbox.get_intent_status() /
    leira.dispatcher.lifecycle.LifecycleKernel.get_run_state(): the
    authoritative answer is always read from ledger_events, never from
    intent_claim_projection.
    """
    rows = ledger.connection.execute(
        """
        SELECT event_type, payload_json, created_at, id FROM ledger_events
        WHERE event_type IN (?, ?)
        AND payload_json LIKE ?
        ORDER BY rowid ASC
        """,
        (CLAIM_ESTABLISHED_EVENT_TYPE, CLAIM_RELEASED_EVENT_TYPE, f'%"intent_id":"{intent_id}"%'),
    ).fetchall()

    intent_events: list[tuple[str, dict, str, str]] = []
    for event_type, payload_json, created_at, event_id in rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("intent_id") != intent_id:
            continue
        intent_events.append((event_type, payload, created_at, event_id))

    return replay_claim_events(intent_events)


def _upsert_claim_projection(
    ledger: LedgerKernel,
    *,
    intent_id: str,
    claim_id: str,
    owner_id: str,
    claimed_at: str,
    last_event_id: str,
) -> bool:
    """Best-effort live insert of one active-claim row. Never raises."""
    try:
        with ledger.connection:
            ledger.connection.execute(
                """
                INSERT INTO intent_claim_projection
                    (intent_id, claim_id, owner_id, claimed_at, last_event_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    claim_id = excluded.claim_id,
                    owner_id = excluded.owner_id,
                    claimed_at = excluded.claimed_at,
                    last_event_id = excluded.last_event_id
                """,
                (intent_id, claim_id, owner_id, claimed_at, last_event_id),
            )
        return True
    except sqlite3.Error:
        return False


def _clear_claim_projection(ledger: LedgerKernel, intent_id: str) -> bool:
    """Best-effort removal of a released claim's row. Never raises."""
    try:
        with ledger.connection:
            ledger.connection.execute(
                "DELETE FROM intent_claim_projection WHERE intent_id = ?", (intent_id,)
            )
        return True
    except sqlite3.Error:
        return False


class ClaimKernel:
    """Exclusive-ownership lock for intents, layered on top of the unmodified inbox/dispatcher."""

    def __init__(self, ledger: LedgerKernel):
        self._ledger = ledger
        ensure_schema(self._ledger)

    def claim_intent(self, intent_id: str, owner_id: str) -> ClaimResult:
        """Claim intent_id for owner_id, or refuse with a typed reason.

        Order: validate owner_id, validate the intent exists and is
        PENDING (reusing leira.inbox.inbox.get_intent_status, never
        redefining intent status), validate no active claim already
        exists (reusing get_claim() above), append the ledger event,
        and only then update the live projection. A failed ledger
        append never leaves a claim recorded anywhere.
        """
        if not isinstance(owner_id, str) or not owner_id:
            self._reject_claim(intent_id, owner_id, "INVALID_OWNER")
            return ClaimResult(success=False, claim_id=None, error_type="INVALID_OWNER")

        status = get_intent_status(self._ledger, intent_id)
        if status is None:
            self._reject_claim(intent_id, owner_id, "UNKNOWN_INTENT")
            return ClaimResult(success=False, claim_id=None, error_type="UNKNOWN_INTENT")
        if status != "PENDING":
            self._reject_claim(intent_id, owner_id, "INVALID_STATUS")
            return ClaimResult(success=False, claim_id=None, error_type="INVALID_STATUS")

        if get_claim(self._ledger, intent_id) is not None:
            self._reject_claim(intent_id, owner_id, "ALREADY_CLAIMED")
            return ClaimResult(success=False, claim_id=None, error_type="ALREADY_CLAIMED")

        claim_id = str(uuid.uuid4())
        append_result = self._ledger.append_event(
            event_type=CLAIM_ESTABLISHED_EVENT_TYPE,
            worker_id=CLAIMS_WORKER_ID,
            payload={
                "claim_id": claim_id,
                "intent_id": intent_id,
                "owner_id": owner_id,
                "artifact": _claim_artifact(claim_id, intent_id, owner_id, "CLAIMED", None),
            },
        )
        if not append_result.success:
            return ClaimResult(success=False, claim_id=None, error_type="STORAGE_FAILURE")

        _upsert_claim_projection(
            self._ledger,
            intent_id=intent_id,
            claim_id=claim_id,
            owner_id=owner_id,
            claimed_at=append_result.created_at,
            last_event_id=append_result.event_id,
        )
        return ClaimResult(success=True, claim_id=claim_id)

    def release_claim(self, intent_id: str, owner_id: str) -> ReleaseResult:
        """Release intent_id's active claim, or refuse with a typed reason.

        Order: validate owner_id, validate an active claim exists,
        validate owner_id matches that claim's owner, append the
        ledger event, and only then clear the live projection row.
        """
        if not isinstance(owner_id, str) or not owner_id:
            self._reject_release(intent_id, owner_id, None, "INVALID_OWNER")
            return ReleaseResult(success=False, error_type="INVALID_OWNER")

        active = get_claim(self._ledger, intent_id)
        if active is None:
            self._reject_release(intent_id, owner_id, None, "NO_ACTIVE_CLAIM")
            return ReleaseResult(success=False, error_type="NO_ACTIVE_CLAIM")

        if active.owner_id != owner_id:
            self._reject_release(intent_id, owner_id, active.claim_id, "OWNER_MISMATCH")
            return ReleaseResult(success=False, error_type="OWNER_MISMATCH")

        append_result = self._ledger.append_event(
            event_type=CLAIM_RELEASED_EVENT_TYPE,
            worker_id=CLAIMS_WORKER_ID,
            payload={
                "claim_id": active.claim_id,
                "intent_id": intent_id,
                "owner_id": owner_id,
                "artifact": _claim_artifact(active.claim_id, intent_id, owner_id, "RELEASED", None),
            },
        )
        if not append_result.success:
            return ReleaseResult(success=False, error_type="STORAGE_FAILURE")

        _clear_claim_projection(self._ledger, intent_id)
        return ReleaseResult(success=True)

    def _reject_claim(self, intent_id: str, owner_id, error_type: str) -> None:
        recorded_owner = owner_id if isinstance(owner_id, str) else repr(owner_id)
        self._ledger.append_event(
            event_type=CLAIM_REJECTED_EVENT_TYPE,
            worker_id=CLAIMS_WORKER_ID,
            payload={
                "intent_id": intent_id,
                "owner_id": recorded_owner,
                "artifact": _claim_artifact(None, intent_id, recorded_owner, "REJECTED", error_type),
            },
        )

    def _reject_release(self, intent_id: str, owner_id, claim_id: str | None, error_type: str) -> None:
        recorded_owner = owner_id if isinstance(owner_id, str) else repr(owner_id)
        self._ledger.append_event(
            event_type=RELEASE_REJECTED_EVENT_TYPE,
            worker_id=CLAIMS_WORKER_ID,
            payload={
                "intent_id": intent_id,
                "owner_id": recorded_owner,
                "claim_id": claim_id,
                "artifact": _claim_artifact(claim_id, intent_id, recorded_owner, "REJECTED", error_type),
            },
        )


def rebuild_claim_projection(ledger: LedgerKernel) -> None:
    """Recompute intent_claim_projection entirely from ledger_events, in one transaction.

    Same shape as leira.inbox.inbox.rebuild_intent_projection() and
    leira.registry.registry.rebuild_worker_projection(): an
    all-or-nothing DELETE-and-replay. Groups events by intent_id (in
    ledger insertion order) and applies replay_claim_events() to each
    group -- the same rule get_claim() and the auditor's
    compute_expected_claim_projection() use, defined once. Released
    claims simply produce no row; an established claim with no
    matching release remains -- an orphan, visible, never removed.
    """
    ensure_schema(ledger)
    conn = ledger.connection

    rows = conn.execute(
        "SELECT id, event_type, payload_json, created_at FROM ledger_events ORDER BY rowid"
    ).fetchall()

    events_by_intent: dict[str, list[tuple[str, dict, str, str]]] = {}
    for event_id, event_type, payload_json, created_at in rows:
        if event_type not in (CLAIM_ESTABLISHED_EVENT_TYPE, CLAIM_RELEASED_EVENT_TYPE):
            continue
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        intent_id = payload.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            continue
        events_by_intent.setdefault(intent_id, []).append(
            (event_type, payload, created_at, event_id)
        )

    with conn:
        conn.execute("DELETE FROM intent_claim_projection")
        for intent_id, intent_events in events_by_intent.items():
            active = replay_claim_events(intent_events)
            if active is None:
                continue
            conn.execute(
                """
                INSERT INTO intent_claim_projection
                    (intent_id, claim_id, owner_id, claimed_at, last_event_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (active.intent_id, active.claim_id, active.owner_id, active.claimed_at, active.last_event_id),
            )
