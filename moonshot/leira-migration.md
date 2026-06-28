# Leira Migration Journal

## 2026-06-28 Milestone: Repository Audit and First Jai Slice

### Milestone

Audited the current Python Leira repository and added an initial Jai migration
area under `jai/`. The first Jai slice models the deterministic event-hash input
preimage used by Python `leira.dispatcher.kernel.compute_event_hash`.

### Observations

- Verified: the repository was clean before migration work began.
- Verified: the Python implementation currently remains under `leira/` and is
  the executable behavioural reference.
- Verified: the v0 Python kernel is a single-writer SQLite ledger with one
  append-only `ledger_events` table, update/delete prevention triggers, canonical
  JSON payload storage, and a SHA-256 hash chain.
- Verified: Python tests are extensive. The previous full run reported 905
  passed and 9 failed, with failures in stress/event-ordering cases and prompt
  inventory expectations.
- Verified: no `jai` executable is currently available on `PATH`.
- Verified: no bundled Jai compiler executable was found under `/root` during
  the initial search.
- Verified: attempting `jai build.jai` from inside `jai/` currently fails before
  compilation with a shell permission error because the command name resolves to
  the `jai/` directory, not to a compiler executable.
- Verified: `python3 -m pytest leira/dispatcher/test_kernel.py -v` passes
  12/12 tests for the Python v0 ledger kernel reference.
- Verified: nearby Moonshot guidance requires separating observations from
  inferences, keeping a lab notebook, and recording decisions with warrants.

### Inferences

- Pretty sure: moving the Python package into `leira/python/` immediately would
  break imports and make the migration less interruptible.
- Pretty sure: an additive `jai/` directory is the lowest-risk layout for early
  coexistence because Python remains untouched and buildable.
- Pretty sure: the ledger hash preimage is a good first reconstruction target
  because later persistence, audit, receipts, and replay behaviour depend on its
  exact determinism.
- Guessing: Jai will simplify some structural result types because explicit
  structs make the success/error result shapes concrete without dataclass
  decorators.

### Lessons Learned

- Preserve Python as the oracle until equivalent Jai behaviour exists and can be
  run.
- Start from deterministic pure functions before reconstructing SQLite-backed
  behaviour.
- Treat compiler availability as an environmental fact, not a reason to invent a
  separate validation story.

### Architectural Changes

- Intentional deviation: Python remains in `leira/` instead of being moved to
  `python/`. This avoids a flag-day package move.
- Intentional limitation: the first Jai slice renders the event-hash input shape
  but does not compute SHA-256 or own full JSON escaping/Unicode normalization.

### Remaining Work

- Install or expose the Jai compiler and run `jai build.jai`.
- Add SHA-256 support or bind to an available C/library implementation.
- Reconstruct canonical JSON validation, including rejection of floats and
  non-string object keys.
- Reconstruct the append-only SQLite ledger.
- Reconstruct `validate_chain`.
- Port the v0 Python kernel tests as behavioural Jai tests.
- Continue with lifecycle, worker seam, projections, audit, inbox, dispatcher,
  registry, claims, receipts, and later engineering-flow modules.

### Research Questions

- How much of Python's canonical JSON and Unicode normalization behaviour should
  be reimplemented directly in Jai versus constrained at Leira's data boundary?
- What is the least fragile way to provide SHA-256 in Jai for a small local
  ledger?
- Does Jai's explicit memory model improve or slow iteration on event-sourced
  data structures compared with Python dataclasses plus SQLite?
- Should Leira's future Jai API preserve Python's typed failure objects exactly,
  or collapse some repeated result shapes after behavioural parity is proven?
