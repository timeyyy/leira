# Leira v0

The smallest honest local event ledger.

This is **not** an agent system and **not** an orchestrator. v0 is the
kernel underneath all of that: a single-process, single-writer, SQLite-backed,
hash-chained, append-only event ledger. Its only job is to preserve truth
mechanically — the machine must be able to say no.

## What's here

```
leira/
  dispatcher/
    __init__.py
    kernel.py       # LedgerKernel: append_event(), validate_chain()
    schema.sql       # ledger_events table + append-only triggers
    test_kernel.py
```

## Security scope

The hash chain protects against:
- accidental mutation
- silent corruption
- replay mismatch (events linked out of order or to the wrong parent)

The hash chain does **not** protect against:
- a malicious local actor with write access to the SQLite file
- a compromised kernel process
- filesystem-level theft or tampering by someone who knows the scheme
- multiple concurrent writers (no locking protocol, no OCC in v0)

This is tamper-*evidence* for one trusted process talking to its own
database — not a security boundary against an adversary who can already
run code or edit files on the same machine.

## Explicitly deferred (not in v0)

Projections, snapshots, workers, adapters, quotas, a conductor loop,
routing, MCP, any LLM provider integration, multi-process access, a
network service, dashboards, a claim registry, belief_promoted events,
convergence receipts, operation contracts.

## Running the tests

```
python3 -m pytest leira/dispatcher/test_kernel.py -v
```
