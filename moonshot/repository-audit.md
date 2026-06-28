# Leira Repository Audit

## Scope

This audit treats the existing Python implementation as the reference
specification for the Jai migration.

## Current Architecture

- `leira/dispatcher/kernel.py` owns the v0 ledger: SQLite storage, append-only
  triggers, canonical payload serialization, event hashes, and chain
  validation.
- `leira/dispatcher/lifecycle.py` layers run lifecycle events onto the ledger.
- `leira/workers/`, `leira/dispatcher/shell.py`, and
  `leira/dispatcher/git.py` attach synchronous worker and adapter seams.
- `leira/projection/`, `leira/inbox/`, `leira/registry/`, `leira/claims/`, and
  `leira/receipts/` add disposable projections and read models over the ledger.
- `leira/audit/` recomputes expected views from ledger history and reports
  deterministic errors without repairing state.
- Newer modules under dispatch, human decision, verification, and engineering
  projection directories follow a deterministic-rendering pattern with strict
  guardrail tests.

## Behavioural Anchors

- Ledger row order is insertion order (`rowid`), not timestamp order.
- Payloads are JSON canonicalized with sorted keys, compact separators, NFC
  string normalization, no floats, and string-only dict keys.
- Event hash input is a sorted-key JSON object containing
  `artifact_hash`, `created_at`, `event_type`, `parent_event_hash`,
  `payload_json`, and `worker_id`.
- Projections are disposable. Ledger history wins over any projection.
- Audits are read-only and deterministic.
- Dispatch is explicit one-call-at-a-time behaviour, not orchestration.

## Test Surface

The Python suite is broad and should be mined for migration milestones rather
than ported wholesale in one pass. Start with pure deterministic units, then move
to persistence and replay.

See `moonshot/behavioural-dependency-map.md` for the living migration dashboard.

## Migration Plan

1. Establish additive `jai/` and `moonshot/` directories while leaving Python in
   place.
2. Reconstruct v0 pure ledger helpers: constants, result structs, canonical
   event-hash input, SHA-256, `compute_event_hash`, and in-memory append
   semantics for caller-supplied canonical simple ASCII inputs.
3. Reconstruct in-memory `validate_chain`.
4. Reconstruct payload canonicalization and its rejection rules.
5. Reconstruct SQLite ledger append and append-only protection.
6. Reconstruct durable `validate_chain` and v0 tests.
7. Move forward version-by-version through lifecycle, workers, adapters,
   projections, audit, inbox, dispatcher, registry, claims, receipts, and the
   deterministic engineering-flow layers.
8. Update the migration journal after every meaningful milestone.
