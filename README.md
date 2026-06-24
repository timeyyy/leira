# Leira v0 / v0.1 / v0.2 / v0.3 / v0.4 / v0.5

The smallest honest local event ledger, the smallest possible gate on
starting work, the smallest possible run lifecycle, the smallest
possible seam for a worker to attach to it, the smallest external
adapter on top of that seam, and the smallest repository witness.

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

v0.3 adds the worker seam: a ``Worker`` protocol (one method, ``wake(run_id,
context) -> WorkerResult``), a ``DeterministicStubWorker`` fixture, and
``run_worker_once()`` to invoke a worker exactly once and record the
result through the existing lifecycle. The call is blocking and
synchronous — a hanging worker hangs the process; v0.3 does not add a
timeout, thread, or async to prevent that. A worker is a test fixture
here, not a mind.

v0.4 adds the shell adapter: ``run_command()`` runs one external command
as an explicit argument list (never ``shell=True``, never a shell
string) and returns a typed ``CommandResult`` — a non-zero exit code or
a timeout is just data, never a kernel failure. ``run_shell_once()``
records the outcome through the same lifecycle as the worker seam,
truncating oversized stdout/stderr deterministically to stay under
``MAX_ARTIFACT_BYTES``. "The first external intelligence invited into
the workshop is the operating system. Not another mind."

v0.5 adds the git adapter: ``inspect_repo()`` runs four read-only git
commands (built entirely on the shell adapter's ``run_command`` — no
process spawned directly here) and returns a typed ``GitStatusResult``:
HEAD sha, current branch, dirty/clean, and the exact
``status --porcelain`` output. A non-repository path, a detached HEAD,
or a missing git executable are all just data, not kernel failures.
``run_git_status_once()`` records the result through the same
lifecycle as the other adapters. Leira never mutates a repository —
no add, commit, push, pull, fetch, merge, or rebase — and never parses
a diff or interprets ``.gitignore``. "Git introduces provenance. Before
inviting another mind, the workshop should learn to remember itself."

## What's here

```
leira/
  dispatcher/
    __init__.py
    kernel.py          # LedgerKernel: append_event(), validate_chain()
    envelope.py         # load_operation(), validate_operation(), load_and_validate()
    lifecycle.py         # LifecycleKernel: create_run(), append_lifecycle_event(), get_run_state()
    worker.py            # Worker protocol, DeterministicStubWorker, run_worker_once()
    shell.py             # run_command(), run_shell_once()
    git.py               # inspect_repo(), run_git_status_once()
    schema.sql           # ledger_events table + append-only triggers
    test_kernel.py
    test_envelope.py
    test_lifecycle.py
    test_worker.py
    test_shell.py
    test_git.py
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

## Security scope: the shell adapter specifically

``run_command`` / ``run_shell_once`` are **not** a sandbox:
- the command inherits the full Leira process environment
- the command runs with the same filesystem permissions as Leira
- v0.4 does not isolate secrets, filesystem access, CPU, or memory

If you would not run a command directly in this process's own shell, do
not hand it to this module.

## Security scope: the git adapter specifically

``inspect_repo`` / ``run_git_status_once`` are read-only and inherit
the same shell-adapter security scope above. They never mutate a
repository, never run ``git add``/``commit``/``push``/``pull``, never
parse diffs, and never interpret ``.gitignore``. Large repositories may
make ``git status --porcelain`` slow; that is inherited honestly, not
optimized away — Leira does not cache or schedule git inspection.

## Explicitly deferred (not in v0 / v0.1 / v0.2 / v0.3 / v0.4 / v0.5)

Projections, snapshots, real workers, OpenAI/Claude/Gemini adapters,
sandboxing, environment isolation, secret filtering, quotas, approval
tokens, a conductor loop, routing, MCP, multi-process access, a network
service, dashboards, a claim registry, belief_promoted events,
convergence receipts, semantic validation, falsifiability evaluation,
operation/run execution, retries, timeouts, cleanup logic, artifact
file storage, parallelism, memory across calls, prompt generation,
agent loops, git add/commit/push/pull/fetch/merge/rebase, branch
creation, diff parsing, .gitignore interpretation, remote
synchronization, caching.

## Running the tests

```
pip install -r requirements.txt
python3 -m pytest leira/dispatcher/ -v
```
