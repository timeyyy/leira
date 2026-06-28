# Leira Behavioural Dependency Map

This map tracks migration order by behaviour, not by file name. The Python
implementation remains the behavioural oracle until the corresponding Jai slice
is independently executable.

| Python module / behaviour | Relevant tests | Dependencies | Current Jai equivalent | Migration status | Next unlockable behaviour |
| --- | --- | --- | --- | --- | --- |
| `leira.dispatcher.kernel.GENESIS_PARENT_HASH` and event-hash preimage shape | `leira/dispatcher/test_kernel.py::test_valid_append_creates_one_ledger_row`, `test_second_append_chains_to_first_event_hash`, `test_unicode_normalization_produces_stable_hashes` | None | `jai/src/ledger_core.jai`, `render_event_hash_input` | Migrated for already-canonical simple ASCII inputs; full NFC/general JSON escaping remains pending | Payload canonicalization |
| `leira.workspace.hashing.sha256(content: bytes) -> str` | `leira/workspace/test_workspace.py::test_sha256_computed_correctly`; external vectors for empty string, `abc`, `hello`, and a 64-byte block-boundary case | Byte iteration, 32-bit arithmetic, deterministic hex formatting | `jai/src/sha256.jai`, `sha256_hex`, `sha256_hex_bytes` | Migrated for byte/string input; verified by Jai test vectors | Later artifact descriptors |
| `leira.dispatcher.kernel.compute_event_hash` full digest | `leira/dispatcher/test_kernel.py::test_unicode_normalization_produces_stable_hashes`, append/validate chain tests | Event-hash preimage shape, SHA-256, JSON string escaping, NFC normalization | `compute_event_hash` in `jai/src/ledger_core.jai` | Partial: migrated for already-canonical simple ASCII inputs and Python-derived vectors; does not perform NFC normalization | Payload canonicalization |
| `leira.dispatcher.kernel.canonicalize_payload` validation | `test_non_string_dict_keys_are_rejected`, `test_float_payload_values_are_rejected`, `test_nan_and_infinity_payload_values_are_rejected`, `test_unicode_normalization_produces_stable_hashes` | JSON value model, key sorting, string escaping, NFC normalization | No Jai equivalent yet | Not started | Validated payload JSON for append and hash computation |
| `LedgerKernel.append_event` in-memory semantics | `test_valid_append_creates_one_ledger_row`, `test_second_append_chains_to_first_event_hash`, invalid event type / worker id checks | Canonical payload JSON supplied by caller, `compute_event_hash`, caller-supplied event IDs and timestamps | `Memory_Ledger`, `append_event_canonical` in `jai/src/ledger_core.jai` | Partial: migrated for in-memory simple ASCII canonical payloads; no UUID, timestamp generation, payload canonicalization, DB errors, or SQLite | Payload canonicalization |
| `LedgerKernel.validate_chain` in-memory semantics | `test_tampering_with_payload_json_causes_validate_chain_to_fail`, `test_validate_chain_passes_for_untampered_chain`, parent-link checks from `test_second_append_chains_to_first_event_hash` | `Memory_Ledger`, append order, stored event hashes, `compute_event_hash` | `validate_chain` in `jai/src/ledger_core.jai` | Partial: migrated for in-memory simple ASCII events; no SQLite rowid, trigger bypass, or durable corruption checks | Payload canonicalization |
| SQLite append-only ledger and durable `validate_chain` | `test_update_on_ledger_events_is_blocked`, `test_delete_on_ledger_events_is_blocked`, `test_tampering_with_payload_json_causes_validate_chain_to_fail`, `test_validate_chain_passes_for_untampered_chain` | Storage, append semantics, real event hashes | No Jai equivalent yet | Not started | v0 ledger parity and lifecycle migration |

## Recommended Next Slice

Implement payload canonicalization for a constrained JSON value model. This
unlocks Python-style append inputs while still avoiding SQLite persistence.
