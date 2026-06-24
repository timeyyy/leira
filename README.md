# Leira v0 / v0.1 / v0.2

The smallest honest local event ledger, the smallest possible gate on
starting work, and the smallest possible run lifecycle.

This is **not** an agent system and **not** an orchestrator. v0 is the
kernel underneath all of that: a single-process, single-writer, SQLite-backed,
hash-chained, append-only event ledger. Its only job is to preserve truth
mechanically — the machine must be able to say no.

v0.1 adds the operation envelope: a small YAML document that must exist
and have the right shape before any operation is allowed to run. The
kernel checks structure only — it never judges whether an objective is
reasonable, a claim is true, or an assumption holds. No envelope, no run.
Nothing more.

v0.2 adds run lifecycle recording: ``operation_validated``, ``run_created``,
``state_running``, ``artifact_written``, ``state_completed``. There is no
``runs`` table — every lifecycle event is just another row in
``ledger_events``, and current run state is always derived from the
ledger, never cached. Transitions are enforced mechanically against a
fixed table (run_created → state_running → artifact_written →
state_completed); a run stuck at any state, or an operation with zero
runs, is not an error — the ledger is a witness, not a supervisor.

## What's here

```
leira/
  dispatcher/
    __init__.py
    kernel.py          # LedgerKernel: append_event(), validate_chain()
    envelope.py         # load_operation(), validate_operation(), load_and_validate()
    lifecycle.py         # LifecycleKernel: create_run(), append_lifecycle_event(), get_run_state()
    schema.sql           # ledger_events table + append-only triggers
    test_kernel.py
    test_envelope.py
    test_lifecycle.py
op.yaml                  # example operation envelope
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

## Explicitly deferred (not in v0 / v0.1 / v0.2)

Projections, snapshots, workers, adapters, quotas, approval tokens, a
conductor loop, routing, MCP, any LLM provider integration,
multi-process access, a network service, dashboards, a claim registry,
belief_promoted events, convergence receipts, semantic validation,
falsifiability evaluation, operation/run execution, retries, timeouts,
cleanup logic, artifact storage.

## Running the tests

```
pip install -r requirements.txt
python3 -m pytest leira/dispatcher/ -v
```
