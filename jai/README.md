# Leira Jai Reconstruction

This directory contains the incremental Jai reconstruction of Leira.

The Python package in `../leira/` remains the executable reference
specification during the migration. Do not move or delete Python modules until
the corresponding Jai behaviour has been reconstructed and verified.

## Current Milestone

The first Jai slice models the deterministic v0 event-hash input shape. It does
not yet implement SQLite storage, append-only triggers, UUID generation,
timestamps, Unicode normalization, full JSON canonicalization, or SHA-256.

Those omissions are intentional: the first milestone establishes a small
buildable Jai surface that mirrors the Python `compute_event_hash` preimage
ordering before expanding into persistence.

## Intended Build

```bash
cd jai
jai build.jai
```

This should compile the test executable into `jai/tests/` once the Jai compiler
is available on `PATH`. In the current environment, `command -v jai` returns no
compiler. Running `jai build.jai` from inside this directory currently reports a
shell-level permission error because `jai` resolves to this directory name
rather than a compiler executable.
