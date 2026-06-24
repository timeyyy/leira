# Leira v0 / v0.1 / v0.2 / v0.3 / v0.4 / v0.5 / v0.6 / v0.7 / v0.8 / v0.9 / v1.0 / v1.1 / v1.2 / v1.3

The smallest honest local event ledger, the smallest possible gate on
starting work, the smallest possible run lifecycle, the smallest
possible seam for a worker to attach to it, the smallest external
adapter on top of that seam, the smallest repository witness, the
smallest general guest door for hosting any worker, the smallest
disposable view over all of it, the smallest machine that checks its
own work, the smallest durable door for intent to enter through, and
the smallest mechanical actuator that can perform one piece of work.

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

v0.6 adds the general worker protocol — the guest door any future
worker attaches through: ``Worker.invoke(inputs: dict) -> WorkerResult``,
three reference workers (``EchoWorker``, ``FailingWorker``,
``ExplodingWorker``), and ``run_worker_once()`` in ``leira/workers/base.py``.
This is deliberately a *different* ``WorkerResult`` than the one in
``leira/dispatcher/worker.py`` — worker success ("the work succeeded")
is never conflated with kernel success ("Leira recorded the event
correctly"). A worker that fails, or raises, still gets a fully
recorded run (the exception is captured as ``error_type="UNEXPECTED"``,
never propagated); only a failed ledger append skips
``state_completed``. Reuses the existing lifecycle/append machinery
exactly as the shell and git adapters do — no new state machine, no
new table, no special path.

v0.7 adds the projection engine: ``operation_state_projection``, a
disposable one-row-per-run cache (``current_state``, ``last_event_id``,
``updated_at``) over the ledger, in ``leira/projection/``.
``LifecycleKernel`` gained an optional ``projection=`` constructor
argument (default ``None``, fully backward compatible) — when given a
``ProjectionEngine``, every successful run-lifecycle append also
updates the projection, but a failed or skipped projection write never
invalidates the ledger append that already succeeded.
``rebuild_projection(ledger)`` recomputes the entire table from
``ledger_events`` from scratch, inside one transaction (all-or-nothing:
a failure partway through rolls back to the previous table state, never
leaving a half-rebuilt projection). ``updated_at`` is always copied
from the ledger event's own ``created_at`` — never ``datetime.now()``.
Deleting the projection loses no truth; ``get_run_state()`` still
derives the real state straight from the ledger regardless of what the
projection says. "History is authoritative. Projections are
convenience. Truth survives."

v0.8 adds the auditor: ``audit(ledger) -> AuditResult`` in
``leira/audit/auditor.py``. It reads the ledger and the projection
table exactly as they stand, recomputes the expected projection *in
memory only* from already-loaded ledger events (never by calling
``rebuild_projection()``), and reports every disagreement as a
deterministic ``CODE:identifier`` string —
``BROKEN_HASH_CHAIN``/``MISSING_PREVIOUS_HASH`` (via
``validate_chain()``, still a fully independent API of its own),
``DUPLICATE_EVENT_ID``, ``MISSING_RUN_ID``, ``ILLEGAL_TRANSITION``
(replaying ``ALLOWED_TRANSITIONS`` directly, never redefining it),
``PROJECTION_MISMATCH``/``PROJECTION_LAST_EVENT_ID_MISMATCH``/
``PROJECTION_UPDATED_AT_MISMATCH``, and ``ARTIFACT_SCHEMA_INVALID``.
The auditor never writes: every check is a SELECT, the same corruption
always produces the same error list in the same order, and a
disagreement between the ledger and a projection is always resolved in
the ledger's favor — reported, never repaired. "Truth lives in
history. Repair belongs elsewhere."

v0.9 adds the inbox: ``InboxKernel.submit_intent(intent_type, payload)
-> SubmitIntentResult`` in ``leira/inbox/inbox.py``, the one door
through which a request for work enters the workshop. Three layers,
kept deliberately separate — Ingress (``inbox_entries``), Authority
(``ledger_events``, via the unmodified ``LedgerKernel.append_event``),
and Representation (``intent_projection``, disposable and rebuildable
via ``rebuild_intent_projection()``) — all sharing the same
``intent_id``. Validation is purely structural (non-empty
``intent_type``, dict ``payload``, JSON-safe by the same rules the
ledger itself enforces); a rejected intent is not an exception, it is
durably recorded with ``status="REJECTED"`` exactly as an accepted one
is recorded ``"PENDING"`` — both are permanent facts about what was
asked for. There is no ``RUNNING``, ``COMPLETED``, or ``FAILED`` status
anywhere in this module, no queue runner, no scheduler, no reaper:
pending intents may accumulate forever, and that is honest, not a bug.
The auditor gained matching, additive checks
(``MISSING_INBOX_ROW``, ``INTENT_STATUS_MISMATCH``,
``INTENT_PROJECTION_MISMATCH`` and its ``_LAST_EVENT_ID``/``_UPDATED_AT``
variants) using the exact same read-only, recompute-in-memory pattern
as the run-projection checks. "Intent enters. Execution waits."

v1.0 adds the single dispatcher: ``dispatch_once(ledger, lifecycle,
intent_id, worker) -> DispatchResult`` in
``leira/dispatcher/dispatcher.py``. The caller supplies both the
intent and the worker explicitly, every time — no lookup, no choice,
no internal loop; dispatching one hundred intents means calling
``dispatch_once`` one hundred times. Execution states
(``PENDING``/``RUNNING``/``COMPLETED``/``FAILED``) are layered onto the
intent lifecycle from v0.9 via three new ledger event types
(``intent_claimed``, ``intent_completed``, ``intent_failed``); the
existing run lifecycle (``state_running``/``artifact_written``) is
reused completely unmodified by wrapping each dispatch in a real run
via the unmodified ``LifecycleKernel``. ``COMPLETED``/``FAILED`` (and
``REJECTED``) are immutable terminal states — a second
``dispatch_once`` on a terminal or already-``RUNNING`` intent is
refused with ``error_type="INVALID_STATUS"``, never a double
execution. The ``Worker`` protocol (``leira/workers/base.py``) gained a
required ``name`` field: recorded as provenance in every artifact and
claim/completion event, never read back to choose or look up a worker.
The auditor gained matching checks reusing the same
``ALLOWED_INTENT_TRANSITIONS`` replay pattern as run transitions
(``DUPLICATE_CLAIM``, ``WORKER_NAME_MISMATCH``, and ``ILLEGAL_TRANSITION``
for any event after a terminal intent state) — and both
``rebuild_intent_projection()`` and the auditor's own expected-state
computation now stop at the first terminal event per intent_id, so a
later illegal event in history can never look like the truth, only
like the violation it is.

v1.1 adds the worker registry: ``WorkerRegistry.register_worker(worker)
-> RegisterResult`` / ``get_worker(worker_name) -> Worker | None`` /
``list_workers() -> list[str]`` in ``leira/registry/registry.py`` -- an
in-process ``dict[str, Worker]``, nothing more. It resolves names; it
does not route, rank, choose, instantiate, scan packages, or load
plugins. Registration follows the same ledger-first ordering as every
other write in this system: validate the worker, append
``worker_registered`` (or ``worker_registration_rejected`` on
``INVALID_WORKER``/``DUPLICATE_WORKER``) through the unmodified
``LedgerKernel.append_event``, and only then update the in-memory
dict -- a failed ledger append never leaves a worker registered in
memory. ``dispatch_by_name(ledger, lifecycle, registry, intent_id,
worker_name)`` is the dispatcher's only new function: it looks the
name up (``UNKNOWN_WORKER`` if missing) and delegates straight to the
unmodified ``dispatch_once`` -- no special path, no fallback, no
worker choice of its own. ``worker_projection`` (``worker_name``,
``registered_at``, ``last_event_id``) is disposable and rebuildable
via ``rebuild_worker_projection()``, exactly like every other
projection in this system -- but the in-memory registry itself is
*not*: a worker object is a live Python reference, and no amount of
ledger replay can reconstruct it, only the record that its name was
once registered. The auditor gained matching checks
(``DUPLICATE_WORKER_REGISTRATION`` and the same
``WORKER_PROJECTION_MISMATCH``/``_LAST_EVENT_ID_MISMATCH``/
``_UPDATED_AT_MISMATCH``/``_UNEXPECTED_ENTRY`` family as every other
projection check) using the same recompute-in-memory,
compare-read-only pattern. "A router chooses. A registry resolves."

v1.2 adds the claim store: ``ClaimKernel.claim_intent(intent_id,
owner_id) -> ClaimResult`` / ``release_claim(intent_id, owner_id) ->
ReleaseResult`` / ``get_claim(ledger, intent_id) -> ClaimInfo | None``
in ``leira/claims/claims.py``. It establishes exclusive ownership of an
intent for an opaque ``owner_id`` string, completely orthogonal to the
existing PENDING/RUNNING/COMPLETED/FAILED status the unmodified inbox
and dispatcher already track -- an intent can be both ``PENDING`` and
actively claimed at once. A claim requires the intent to exist, be
``PENDING``, and have no existing active claim; only one of the v1.2
spec's literal event names had to change to fit alongside v1.0:
successful claims append ``intent_claim_established`` rather than
``intent_claimed``, since that name already belongs, with a different
shape, to the dispatcher's own intent-execution events -- reusing it
would have corrupted ``leira.inbox.inbox.rebuild_intent_projection``
and several existing auditor checks, which this version is told not
to redesign. Every other event name
(``intent_released``/``intent_claim_rejected``/``intent_release_rejected``)
matches the spec exactly. ``dispatch_and_track(ledger, lifecycle,
claims, intent_id, owner_id, worker)`` brackets the unmodified
``dispatch_once`` with claim-then-release; a claim failure means
``dispatch_once`` is never called, and a release failure after
execution is reported on ``DispatchResult``'s new, optional
``release_error_type`` field rather than retried or hidden -- the
claim simply stays an orphan, fully visible via ``get_claim()`` and to
the auditor. There are no leases, no expiration, no liveness checks,
and no orphan cleanup: a crashed owner's claim remains active forever,
honestly. ``intent_claim_projection`` is disposable and rebuildable via
``rebuild_claim_projection()``, sharing one replay rule
(``replay_claim_events()``) with ``get_claim()`` and the auditor's own
expected-projection computation. The auditor gained matching checks
(``DUPLICATE_ACTIVE_CLAIM``, ``RELEASE_OWNER_MISMATCH``, and the usual
``CLAIM_PROJECTION_MISMATCH``/``_LAST_EVENT_ID_MISMATCH``/
``_UPDATED_AT_MISMATCH``/``_UNEXPECTED_ENTRY`` family) -- an orphaned
claim is, by design, never one of them. "Claims coordinate. They do
not decide."

v1.3 adds receipt bundles: ``get_receipt_bundle(ledger, intent_id) ->
ReceiptBundle | None`` / ``list_receipt_events(ledger, intent_id) ->
list[LedgerEvent]`` / ``export_receipt_bundle(ledger, intent_id) ->
dict`` in ``leira/receipts/receipts.py``. A bundle is every ledger
event sharing an intent_id, exposed together in true ledger order
(``rowid``, never ``created_at`` -- timestamps may collide, insertion
order cannot) -- a view, never a second source of truth. Most
intent-scoped events carry ``intent_id`` directly; ``state_running``/
``artifact_written`` only carry ``run_id``, so ``list_receipt_events``
bridges through ``run_created`` (always created with
``operation_id=intent_id`` by the unmodified
``leira.dispatcher.dispatcher.dispatch_once``) to find them, entirely
through read-only queries against the existing, public
``LedgerKernel.connection`` -- no change to the ledger, lifecycle,
inbox, dispatcher, registry, or claim store. This version also adds
the one small, purely additive piece the spec assumed already
existed: a ``LedgerEvent`` dataclass in ``leira/dispatcher/kernel.py``
(zero changes to ``LedgerKernel`` itself), since no prior version had
ever needed a stable, typed row shape rather than a one-off query.
``export_receipt_bundle`` returns a plain, JSON-ready dict; exporting
the same bundle twice and serializing both with
``json.dumps(..., sort_keys=True, separators=(",", ":"))`` is
byte-identical, by construction. ``receipt_projection`` (``intent_id``,
``first_event_id``, ``last_event_id``, ``event_count``, ``updated_at``)
is disposable and rebuildable via ``rebuild_receipt_projection()`` --
unlike every other projection in this system, nothing else eagerly
keeps it live, since a receipt is only ever materialized on demand;
the auditor's matching checks
(``RECEIPT_FIRST_EVENT_ID_MISMATCH``/``_LAST_EVENT_ID_MISMATCH``/
``_EVENT_COUNT_MISMATCH``/``_UPDATED_AT_MISMATCH``/
``_PROJECTION_UNEXPECTED_ENTRY``) therefore only validate rows that
actually exist, rather than requiring universal coverage.
``RECEIPT_EVENT_COUNT_MISMATCH`` doubles as the bundle-completeness
check the spec calls for. "Receipts expose witnesses. They do not
build the story."

## What's here

```
leira/
  dispatcher/
    __init__.py
    kernel.py          # LedgerKernel: append_event(), validate_chain(); LedgerEvent dataclass
    envelope.py         # load_operation(), validate_operation(), load_and_validate()
    lifecycle.py         # LifecycleKernel: create_run(), append_lifecycle_event(), get_run_state()
    worker.py            # Worker protocol, DeterministicStubWorker, run_worker_once()
    shell.py             # run_command(), run_shell_once()
    git.py               # inspect_repo(), run_git_status_once()
    dispatcher.py         # dispatch_once() -> DispatchResult, dispatch_by_name(), dispatch_and_track()
    schema.sql           # ledger_events table + append-only triggers
    test_kernel.py
    test_envelope.py
    test_lifecycle.py
    test_worker.py
    test_shell.py
    test_git.py
    test_dispatcher.py
  workers/
    __init__.py
    base.py              # Worker protocol (name + invoke()), EchoWorker/FailingWorker/ExplodingWorker, run_worker_once()
    test_base.py
  projection/
    __init__.py
    state.py              # ProjectionEngine: get_current_state(), update_from_event()
    rebuild.py             # rebuild_projection()
    test_projection.py
  audit/
    __init__.py
    auditor.py             # audit() -> AuditResult
    test_auditor.py
  inbox/
    __init__.py
    inbox.py               # InboxKernel.submit_intent(), get_intent_status(), rebuild_intent_projection()
    test_inbox.py
  registry/
    __init__.py
    registry.py             # WorkerRegistry, RegisterResult, rebuild_worker_projection()
    test_registry.py
  claims/
    __init__.py
    claims.py                # ClaimKernel.claim_intent()/release_claim(), get_claim(), rebuild_claim_projection()
    test_claims.py
  receipts/
    __init__.py
    receipts.py               # get_receipt_bundle(), list_receipt_events(), export_receipt_bundle(), rebuild_receipt_projection()
    test_receipts.py
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

## Explicitly deferred (not in v0 / v0.1 / v0.2 / v0.3 / v0.4 / v0.5 / v0.6 / v0.7 / v0.8 / v0.9 / v1.0 / v1.1 / v1.2 / v1.3)

Queues, scheduling beyond one explicit call, routing, priorities,
capabilities, tags, automatic/fuzzy worker selection, dependency
injection, plugin loading, automatic discovery, dynamic imports,
module scanning, load balancing, worker pools, automatic claiming,
multiple/parallel dispatch, distributed coordination, leases, claim
expiration, claim stealing, liveness checks, orphan cleanup, stale-
intent cleanup, any reaper or cleaner, automatic repair/recovery,
repair suggestions, rebuild during audit, anomaly scoring, monitoring
or background audits, materialized views, indexing optimization,
caching layers, a query planner, search, vector databases, embeddings,
analytics, dashboards, reports, summaries, compression, scoring,
subscriptions, streaming, pubsub, notifications, LLM projections or
explanations, OpenAI/Claude/Gemini adapters, MCP, sandboxing,
environment isolation, secret filtering, quotas, approval tokens, a
conductor loop, multi-process access, a network service,
belief_promoted events, convergence receipts, semantic validation,
falsifiability evaluation, retries, timeouts, cleanup logic, artifact
file storage, parallelism, memory across calls, prompt generation,
conversation history, agent loops, worker orchestration, tool choice,
persistent worker objects across process restarts, external
verification signatures, receipt signing, git
add/commit/push/pull/fetch/merge/rebase, branch creation, diff parsing,
.gitignore interpretation, remote synchronization.

## Running the tests

```
pip install -r requirements.txt
python3 -m pytest leira/ -v
```
