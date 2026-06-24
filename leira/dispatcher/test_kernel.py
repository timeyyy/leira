import sqlite3
import unicodedata

import pytest

from leira.dispatcher.kernel import (
    GENESIS_PARENT_HASH,
    LedgerKernel,
    canonicalize_payload,
    compute_event_hash,
    PayloadValidationError,
)


@pytest.fixture
def kernel(tmp_path):
    db_path = tmp_path / "ledger.sqlite3"
    k = LedgerKernel(str(db_path))
    yield k
    k.close()


def _raw_rows(kernel):
    return kernel._conn.execute(
        "SELECT id, parent_event_hash, event_type, worker_id, payload_json, "
        "artifact_hash, event_hash, created_at FROM ledger_events ORDER BY rowid"
    ).fetchall()


def test_valid_append_creates_one_ledger_row(kernel):
    result = kernel.append_event(
        event_type="task_started",
        worker_id="worker-1",
        payload={"task": "build"},
    )
    assert result.success
    assert result.event_id is not None
    assert result.event_hash is not None

    rows = _raw_rows(kernel)
    assert len(rows) == 1
    assert rows[0][1] == GENESIS_PARENT_HASH


def test_second_append_chains_to_first_event_hash(kernel):
    first = kernel.append_event(
        event_type="task_started", worker_id="worker-1", payload={"n": 1}
    )
    second = kernel.append_event(
        event_type="task_finished", worker_id="worker-1", payload={"n": 2}
    )
    assert first.success and second.success

    rows = _raw_rows(kernel)
    assert rows[1][1] == first.event_hash
    assert rows[1][1] == rows[0][6]


def test_update_on_ledger_events_is_blocked(kernel):
    kernel.append_event(
        event_type="task_started", worker_id="worker-1", payload={"n": 1}
    )
    with pytest.raises(sqlite3.IntegrityError):
        kernel._conn.execute(
            "UPDATE ledger_events SET event_type = 'tampered'"
        )


def test_delete_on_ledger_events_is_blocked(kernel):
    kernel.append_event(
        event_type="task_started", worker_id="worker-1", payload={"n": 1}
    )
    with pytest.raises(sqlite3.IntegrityError):
        kernel._conn.execute("DELETE FROM ledger_events")


def test_tampering_with_payload_json_causes_validate_chain_to_fail(kernel):
    kernel.append_event(
        event_type="task_started", worker_id="worker-1", payload={"n": 1}
    )
    valid = kernel.validate_chain()
    assert valid.success

    # Bypass the append-only triggers directly via the underlying DB file
    # connection to simulate out-of-band corruption (e.g. a different
    # process editing the raw sqlite file). This must NOT go through the
    # kernel's own write path.
    rows = _raw_rows(kernel)
    event_id = rows[0][0]
    kernel._conn.execute("DROP TRIGGER trg_ledger_events_no_update")
    kernel._conn.execute(
        "UPDATE ledger_events SET payload_json = '{\"n\":999}' WHERE id = ?",
        (event_id,),
    )
    kernel._conn.commit()

    result = kernel.validate_chain()
    assert not result.success
    assert result.error_type == "HASH_MISMATCH"
    assert result.failed_event_id == event_id


def test_non_string_dict_keys_are_rejected():
    with pytest.raises(PayloadValidationError):
        canonicalize_payload({1: "value"})


def test_non_string_dict_keys_rejected_via_append_event(kernel):
    result = kernel.append_event(
        event_type="task_started",
        worker_id="worker-1",
        payload={1: "value"},
    )
    assert not result.success
    assert result.error_type == "INVALID_PAYLOAD"
    assert _raw_rows(kernel) == []


def test_float_payload_values_are_rejected():
    with pytest.raises(PayloadValidationError):
        canonicalize_payload({"score": 1.5})


def test_nan_and_infinity_payload_values_are_rejected():
    with pytest.raises(PayloadValidationError):
        canonicalize_payload({"score": float("nan")})
    with pytest.raises(PayloadValidationError):
        canonicalize_payload({"score": float("inf")})


def test_float_payload_rejected_via_append_event(kernel):
    result = kernel.append_event(
        event_type="task_started",
        worker_id="worker-1",
        payload={"score": 1.5},
    )
    assert not result.success
    assert result.error_type == "INVALID_PAYLOAD"
    assert _raw_rows(kernel) == []


def test_unicode_normalization_produces_stable_hashes():
    # "e" + combining acute accent (NFD) vs precomposed "é" (NFC).
    nfd = "Café"
    nfc = unicodedata.normalize("NFC", nfd)
    assert nfd != nfc  # sanity: genuinely different byte sequences

    json_from_nfd = canonicalize_payload({"name": nfd})
    json_from_nfc = canonicalize_payload({"name": nfc})
    assert json_from_nfd == json_from_nfc

    hash_from_nfd = compute_event_hash(
        parent_event_hash=GENESIS_PARENT_HASH,
        event_type="x",
        worker_id="w",
        artifact_hash=None,
        payload_json=json_from_nfd,
        created_at="2026-01-01T00:00:00+00:00",
    )
    hash_from_nfc = compute_event_hash(
        parent_event_hash=GENESIS_PARENT_HASH,
        event_type="x",
        worker_id="w",
        artifact_hash=None,
        payload_json=json_from_nfc,
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert hash_from_nfd == hash_from_nfc


def test_validate_chain_passes_for_untampered_chain(kernel):
    for i in range(5):
        result = kernel.append_event(
            event_type="step",
            worker_id="worker-1",
            payload={"i": i, "label": f"step-{i}"},
        )
        assert result.success

    result = kernel.validate_chain()
    assert result.success
    assert result.events_checked == 5
    assert result.failed_event_id is None
