import inspect

import pytest

from leira.audit.auditor import audit
from leira.dispatcher.kernel import LedgerKernel
from leira.projection.rebuild import rebuild_projection
from leira.receipts.receipts import export_receipt_bundle, get_receipt_bundle
from leira.workspace.hashing import sha256
from leira.workspace.workspace import Workspace
from leira.workspace.paths import WorkspaceError


@pytest.fixture
def ledger(tmp_path):
    k = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    yield k
    k.close()


@pytest.fixture
def workspace(tmp_path, ledger):
    return Workspace(ledger, tmp_path / "workspace")


def _event_count(ledger, event_type):
    return ledger.connection.execute(
        "SELECT COUNT(*) FROM ledger_events WHERE event_type = ?", (event_type,)
    ).fetchone()[0]


def _artifact_row(ledger, artifact_id):
    return ledger.connection.execute(
        "SELECT artifact_id, intent_id, relative_path, sha256, size_bytes, created_at, last_event_id "
        "FROM artifact_projection WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()


def _artifact_path(workspace, descriptor):
    return workspace.root / "intents" / descriptor.intent_id / "artifacts" / descriptor.relative_path


def test_artifact_write_succeeds(workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    assert descriptor.intent_id == "intent-1"
    assert descriptor.relative_path == "output.txt"
    assert _artifact_path(workspace, descriptor).read_bytes() == b"hello"


def test_artifact_read_succeeds(workspace):
    workspace.write_artifact("intent-1", "output.txt", b"hello")
    assert workspace.read_artifact("intent-1", "output.txt") == b"hello"


def test_sha256_computed_correctly(workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    assert descriptor.sha256 == sha256(b"hello")


def test_size_bytes_correct(workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    assert descriptor.size_bytes == 5


def test_duplicate_write_rejected(workspace):
    workspace.write_artifact("intent-1", "output.txt", b"hello")
    with pytest.raises(WorkspaceError) as exc:
        workspace.write_artifact("intent-1", "output.txt", b"again")
    assert exc.value.error_type == "ALREADY_EXISTS"
    assert workspace.read_artifact("intent-1", "output.txt") == b"hello"


def test_artifact_file_written_recorded(ledger, workspace):
    workspace.write_artifact("intent-1", "output.txt", b"hello")
    assert _event_count(ledger, "artifact_file_written") == 1


def test_artifact_write_rejected_recorded(ledger, workspace):
    workspace.write_artifact("intent-1", "output.txt", b"hello")
    with pytest.raises(WorkspaceError):
        workspace.write_artifact("intent-1", "output.txt", b"again")
    assert _event_count(ledger, "artifact_write_rejected") == 1


def test_projection_rebuilt_correctly(ledger, workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    before = _artifact_row(ledger, descriptor.artifact_id)
    ledger.connection.execute("DELETE FROM artifact_projection")
    ledger.connection.commit()

    rebuild_projection(ledger)
    assert _artifact_row(ledger, descriptor.artifact_id) == before


def test_created_at_derived_from_ledger_timestamps(ledger, workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    created_at = ledger.connection.execute(
        "SELECT created_at FROM ledger_events WHERE event_type = 'artifact_file_written'"
    ).fetchone()[0]
    assert descriptor.created_at == created_at
    assert _artifact_row(ledger, descriptor.artifact_id)[5] == created_at


def test_receipt_bundle_references_descriptors(ledger, workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    bundle = get_receipt_bundle(ledger, "intent-1")
    assert bundle is not None
    assert bundle.artifacts == [descriptor]


def test_receipt_bundle_does_not_embed_bytes(ledger, workspace):
    workspace.write_artifact("intent-1", "output.txt", b"hello")
    exported = export_receipt_bundle(ledger, "intent-1")
    assert exported["artifacts"]
    assert b"hello" not in repr(exported).encode()


def test_missing_file_detected_by_audit(ledger, workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    _artifact_path(workspace, descriptor).unlink()
    result = audit(ledger, workspace.root)
    assert not result.success
    assert f"MISSING_ARTIFACT_FILE:{descriptor.artifact_id}" in result.errors


def test_size_mismatch_detected_by_audit(ledger, workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    _artifact_path(workspace, descriptor).write_bytes(b"hello!")
    result = audit(ledger, workspace.root)
    assert not result.success
    assert f"SIZE_MISMATCH:{descriptor.artifact_id}" in result.errors


def test_hash_mismatch_detected_by_audit(ledger, workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    _artifact_path(workspace, descriptor).write_bytes(b"HELLO")
    result = audit(ledger, workspace.root)
    assert not result.success
    assert f"HASH_MISMATCH:{descriptor.artifact_id}" in result.errors


def test_hash_mismatch_with_identical_size_detected(ledger, workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"abcde")
    _artifact_path(workspace, descriptor).write_bytes(b"abXde")
    result = audit(ledger, workspace.root)
    assert not result.success
    assert f"HASH_MISMATCH:{descriptor.artifact_id}" in result.errors


def test_path_traversal_rejected(workspace):
    with pytest.raises(WorkspaceError) as exc:
        workspace.write_artifact("intent-1", "../output.txt", b"hello")
    assert exc.value.error_type == "PATH_TRAVERSAL"


def test_absolute_paths_rejected(workspace):
    with pytest.raises(WorkspaceError) as exc:
        workspace.write_artifact("intent-1", "/tmp/output.txt", b"hello")
    assert exc.value.error_type == "INVALID_PATH"


def test_empty_paths_rejected(workspace):
    with pytest.raises(WorkspaceError) as exc:
        workspace.write_artifact("intent-1", "", b"hello")
    assert exc.value.error_type == "INVALID_PATH"


def test_exclusive_write_mode_prevents_overwrite(workspace):
    source = inspect.getsource(Workspace.write_artifact)
    assert 'open(path, "xb")' in source


def test_audit_remains_read_only(ledger, workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    before_events = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    before_projection = ledger.connection.execute(
        "SELECT * FROM artifact_projection ORDER BY artifact_id"
    ).fetchall()
    before_bytes = _artifact_path(workspace, descriptor).read_bytes()

    result = audit(ledger, workspace.root)

    after_events = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    after_projection = ledger.connection.execute(
        "SELECT * FROM artifact_projection ORDER BY artifact_id"
    ).fetchall()
    after_bytes = _artifact_path(workspace, descriptor).read_bytes()
    assert result.success
    assert before_events == after_events
    assert before_projection == after_projection
    assert before_bytes == after_bytes


def test_rebuild_deterministic(ledger, workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    rebuild_projection(ledger)
    first = _artifact_row(ledger, descriptor.artifact_id)
    rebuild_projection(ledger)
    second = _artifact_row(ledger, descriptor.artifact_id)
    assert first == second


def test_rebuild_idempotent(ledger, workspace):
    workspace.write_artifact("intent-1", "output.txt", b"hello")
    rebuild_projection(ledger)
    first = ledger.connection.execute("SELECT * FROM artifact_projection").fetchall()
    rebuild_projection(ledger)
    second = ledger.connection.execute("SELECT * FROM artifact_projection").fetchall()
    assert first == second


def test_projection_loss_recoverable(ledger, workspace):
    descriptor = workspace.write_artifact("intent-1", "output.txt", b"hello")
    ledger.connection.execute("DROP TABLE artifact_projection")
    ledger.connection.commit()
    rebuild_projection(ledger)
    assert workspace.get_artifact(descriptor.artifact_id) == descriptor


def test_validate_chain_still_succeeds(ledger, workspace):
    workspace.write_artifact("intent-1", "output.txt", b"hello")
    assert ledger.validate_chain().success


def test_hundred_artifact_stress_test(ledger, workspace):
    descriptors = [
        workspace.write_artifact("intent-1", f"artifact-{i}.txt", f"value-{i}".encode())
        for i in range(100)
    ]
    for descriptor in descriptors:
        assert _artifact_path(workspace, descriptor).read_bytes()
        assert workspace.get_artifact(descriptor.artifact_id) == descriptor

    rebuild_projection(ledger)
    assert audit(ledger, workspace.root).success
