-- Leira v0 ledger schema.
--
-- One table. Append-only, enforced by triggers (not just convention).
-- No projections, no snapshots, no multi-writer concurrency control.

CREATE TABLE IF NOT EXISTS ledger_events (
    id                TEXT PRIMARY KEY,
    operation_id      TEXT,
    parent_event_hash TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    worker_id         TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    artifact_hash     TEXT,
    event_hash        TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_events_operation_id
    ON ledger_events (operation_id);

-- Append-only enforcement: no UPDATE, no DELETE, ever.
CREATE TRIGGER IF NOT EXISTS trg_ledger_events_no_update
BEFORE UPDATE ON ledger_events
BEGIN
    SELECT RAISE(ABORT, 'ledger_events is append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_ledger_events_no_delete
BEFORE DELETE ON ledger_events
BEGIN
    SELECT RAISE(ABORT, 'ledger_events is append-only: DELETE is forbidden');
END;
