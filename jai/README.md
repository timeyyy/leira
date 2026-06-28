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

## Build And Test

```bash
jai/run_tests.sh
```

The script uses `JAI_COMPILER_PATH` when set, then falls back to the Honkerworks
local compiler path `/root/programming/jai/bin/jai-linux`, and finally to `jai`
on `PATH`.

Manual equivalent:

```bash
cd jai
/root/programming/jai/bin/jai-linux build.jai
./tests/test_ledger_core
```
