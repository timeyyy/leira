# Leira Behavioural Dependency Map

This map tracks migration order by behaviour, not by file name. The Python
implementation remains the behavioural oracle until the corresponding Jai slice
is independently executable.

| Python module / behaviour | Relevant tests | Dependencies | Current Jai equivalent | Migration status | Next unlockable behaviour |
| --- | --- | --- | --- | --- | --- |
| `leira.dispatcher.kernel.GENESIS_PARENT_HASH` and event-hash preimage shape | `leira/dispatcher/test_kernel.py::test_valid_append_creates_one_ledger_row`, `test_second_append_chains_to_first_event_hash`, `test_unicode_normalization_produces_stable_hashes` | None | `jai/src/ledger_core.jai`, `render_event_hash_input` | Partial: preimage shape migrated; full `compute_event_hash` waits on canonical JSON and SHA-256 integration | `compute_event_hash` returning a real SHA-256 digest |
| `leira.workspace.hashing.sha256(content: bytes) -> str` | `leira/workspace/test_workspace.py::test_sha256_computed_correctly`; external vectors for empty string, `abc`, `hello`, and a 64-byte block-boundary case | Byte iteration, 32-bit arithmetic, deterministic hex formatting | `jai/src/sha256.jai`, `sha256_hex`, `sha256_hex_bytes` | Migrated for byte/string input; verified by Jai test vectors | Use SHA-256 in `compute_event_hash`; later artifact descriptors |
| `leira.dispatcher.kernel.compute_event_hash` full digest | `leira/dispatcher/test_kernel.py::test_unicode_normalization_produces_stable_hashes`, append/validate chain tests | Event-hash preimage shape, SHA-256, JSON string escaping, NFC normalization | No full Jai equivalent yet | Not started | Deterministic event hashes without persistence |
| `leira.dispatcher.kernel.canonicalize_payload` validation | `test_non_string_dict_keys_are_rejected`, `test_float_payload_values_are_rejected`, `test_nan_and_infinity_payload_values_are_rejected`, `test_unicode_normalization_produces_stable_hashes` | JSON value model, key sorting, string escaping, NFC normalization | No Jai equivalent yet | Not started | Validated payload JSON for append and hash computation |
| `LedgerKernel.append_event` in-memory semantics | `test_valid_append_creates_one_ledger_row`, `test_second_append_chains_to_first_event_hash`, invalid payload append tests | Canonical payload JSON, `compute_event_hash`, event IDs/timestamps, storage choice | Result structs only | Not started | Appendable Jai ledger without SQLite |
| SQLite append-only ledger and `validate_chain` | `test_update_on_ledger_events_is_blocked`, `test_delete_on_ledger_events_is_blocked`, `test_tampering_with_payload_json_causes_validate_chain_to_fail`, `test_validate_chain_passes_for_untampered_chain` | Storage, append semantics, real event hashes | No Jai equivalent yet | Not started | v0 ledger parity and lifecycle migration |

## Recommended Next Slice

Integrate `sha256_hex` with `render_event_hash_input` to implement a Jai
`compute_event_hash` for already-canonical simple ASCII inputs. This is smaller
than full payload canonicalization and directly unlocks hash-chain tests.
