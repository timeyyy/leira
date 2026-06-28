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
- Verified: Python tests are extensive, but the exact count is not approximate:
  `python3 -m pytest leira/ --collect-only -q` collected 914 tests.
- Verified: a full available Python suite run with
  `/usr/bin/time -p python3 -m pytest leira/ -q` reported 905 passed and 9
  failed. Pytest reported `390.83s (0:06:30)`; `/usr/bin/time` reported
  `real 372.65`.
- Verified: full-suite failures on 2026-06-28 were:
  `leira/archive/test_archive.py::test_imported_artifact_hashes_verified`,
  `leira/archive/test_archive.py::test_replay_does_not_invoke_shell_adapter`,
  `leira/archive/test_archive.py::test_hundred_intent_archive_replay_stress`,
  `leira/environment/test_environment.py::test_hundred_snapshot_stress_test`,
  `leira/project_state/test_project_state.py::test_prompt_files_are_inventoried`,
  `leira/prompt_drafter/test_prompt_drafter.py::test_deterministic_refusal_output`,
  `leira/prompt_drafter/test_prompt_drafter.py::test_prompt_20_postponed_means_prompt_20_is_not_selected`,
  `leira/provenance/test_git_provenance.py::test_hundred_snapshot_stress_test`,
  and `leira/sessions/test_sessions.py::test_hundred_intent_stress_test`.
- Verified: `jai` is not on `PATH`, but Honkerworks repositories do not rely
  only on `PATH`. `jai-moonshot/src/moonshot/cli.jai`, Genesis documentation,
  and Honkerworks receipts use `/root/programming/jai/bin/jai-linux`.
- Verified: `/root/programming/jai/bin/jai-linux` exists and is executable in
  this environment.
- Verified: `/root/programming/jai/bin/jai-linux build.jai` in `jai/` compiled
  the current Jai milestone successfully; the compiler reported total time
  `0.554254` to `1.235438` seconds across observed runs.
- Verified: `./jai/tests/test_ledger_core` executes successfully.
- Verified: `jai/run_tests.sh` now compiles and executes the current Jai
  milestone from the repository root.
- Verified: `python3 -m pytest leira/dispatcher/test_kernel.py -v` passes
  12/12 tests for the Python v0 ledger kernel reference.
- Verified: the Python tests relevant to the current migrated ledger-core slice
  are primarily `leira/dispatcher/test_kernel.py`. Within that file, the current
  Jai slice most directly corresponds to `GENESIS_PARENT_HASH`, the
  `compute_event_hash` preimage shape exercised by
  `test_unicode_normalization_produces_stable_hashes`, and the hash-chain
  expectations exercised by append/validate tests.
  `leira/workspace/test_workspace.py::test_sha256_computed_correctly` becomes
  relevant when SHA-256 itself is migrated.
- Verified: nearby Moonshot guidance requires separating observations from
  inferences, keeping a lab notebook, and recording decisions with warrants.

### Corrected Assumptions

- Corrected: the earlier "no bundled Jai compiler executable was found under
  `/root`" conclusion was false. The search missed the established Honkerworks
  compiler path `/root/programming/jai/bin/jai-linux`.
- Corrected: the earlier statement that `jai build.jai` failed because no
  compiler was available was incomplete. The failure happened because `jai` was
  not on `PATH` and the shell resolved `jai` to the local `jai/` directory when
  run from inside it. The actual compiler path works.
- Corrected: do not rely on a remembered "~400 slow tests" claim. The measured
  suite currently collects 914 tests.
- Corrected: the 12-test run was not the full Leira suite. It was selected
  because `leira/dispatcher/test_kernel.py` is the Python reference suite most
  directly relevant to the migrated ledger-core slice.
- Corrected: broader tests were not missed in the audit. They were collected
  and run separately after the slice-specific test.

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
- Search local project conventions before declaring a tool unavailable. In this
  workspace, `/root/programming/jai/bin/jai-linux` is the reliable Jai compiler
  invocation even though `jai` is not on `PATH`.
- Every migration milestone must have an executable local command. For the
  current Jai slice that command is `jai/run_tests.sh`.

### Architectural Changes

- Intentional deviation: Python remains in `leira/` instead of being moved to
  `python/`. This avoids a flag-day package move.
- Intentional limitation: the first Jai slice renders the event-hash input shape
  but does not compute SHA-256 or own full JSON escaping/Unicode normalization.

### Remaining Work

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

## 2026-06-28 Milestone: SHA-256 Primitive

### Milestone

Added the next smallest behavioural slice: deterministic SHA-256 hex digests in
Jai for byte/string input. This ports the behaviour of
`leira.workspace.hashing.sha256` without migrating workspace persistence.

### Observations

- Verified: `leira.workspace.hashing.sha256` is a pure wrapper around
  `hashlib.sha256(content).hexdigest()`.
- Verified: `leira/workspace/test_workspace.py::test_sha256_computed_correctly`
  is the direct Python reference test for artifact digest behaviour.
- Verified: the Jai implementation is self-contained in `jai/src/sha256.jai`;
  it does not shell out and does not use Python or OpenSSL.
- Verified: Jai SHA-256 tests cover the empty string, `hello`, `abc`, and a
  64-byte input that crosses the padding/block boundary.
- Verified: the first SHA-256 implementation compiled but failed the empty
  string vector. The defect was in big-endian word loading: `cast(u32)
  bytes[offset] << 24` did not produce the intended promoted-byte shift.
- Verified: rewriting byte promotion into explicit temporaries fixed the digest
  vectors.
- Verified: `jai/run_tests.sh` compiles and executes the current Jai tests
  successfully after the SHA-256 addition.
- Verified: `python3 -m pytest leira/workspace/test_workspace.py::test_sha256_computed_correctly -q`
  passes for the Python reference.

### Inferences

- Pretty sure: explicit intermediate variables are safer than compact cast/shift
  expressions in Jai code written by migration agents.
- Pretty sure: SHA-256 is a useful bridge primitive because it unlocks both
  `compute_event_hash` and future artifact descriptor migration.
- Guessing: keeping SHA-256 self-contained will improve portability for the
  research migration, at the cost of maintaining a small crypto primitive that
  should remain narrowly scoped and vector-tested.

### Lessons Learned

- Compile success is not enough for numeric code; known vectors are mandatory.
- For bit-level Jai code, prefer boring explicit casts and temporaries over
  expression density.

### Architectural Changes

- Intentional deviation: Python delegates SHA-256 to `hashlib`; Jai now has a
  local SHA-256 implementation. Behaviour is intended to match Python's digest
  output exactly for byte input.
- Intentional limitation: this slice does not yet connect SHA-256 to
  `render_event_hash_input`, artifact files, or SQLite ledger rows.

### Remaining Work

- Implement Jai `compute_event_hash` by hashing `render_event_hash_input`.
- Add JSON string escaping and canonical payload support.
- Reconstruct append semantics and chain validation.

### Research Questions

- Should the Jai implementation keep a local SHA-256 primitive long term, or
  replace it with a vetted library binding once the migration reaches production
  hardening?
- What other Jai expression forms need defensive style guidance for migration
  agents?

### Recommended Next Slice

Implement `compute_event_hash` for already-canonical simple ASCII inputs by
combining `render_event_hash_input` with `sha256_hex`. This is the next smallest
slice because both dependencies now exist and it unlocks hash-chain behaviour
before SQLite persistence.
