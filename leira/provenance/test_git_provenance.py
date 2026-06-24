import inspect
import subprocess
from pathlib import Path

import pytest

from leira.audit import auditor as auditor_module
from leira.audit.auditor import audit
from leira.claims.claims import ClaimKernel, get_claim
from leira.dispatcher.dispatcher import (
    dispatch_with_provenance,
    dispatch_with_provenance_and_track,
)
from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.inbox.inbox import InboxKernel, get_intent_status
from leira.projection.rebuild import rebuild_projection
from leira.projection.state import ProjectionEngine
from leira.provenance import git_provenance as provenance_module
from leira.provenance.git_provenance import (
    GitProvenance,
    ProvenanceSnapshot,
    capture_provenance,
    get_provenance,
)
from leira.receipts.receipts import get_receipt_bundle
from leira.workers.base import EchoWorker, WorkerResult


def _run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(path: Path, branch: str = "leira-test") -> Path:
    _run(["git", "init", "-q", str(path)])
    _run(["git", "-C", str(path), "checkout", "-q", "-b", branch])
    (path / "README.txt").write_text("hello\n", encoding="utf-8")
    _run(["git", "-C", str(path), "add", "README.txt"])
    _run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Leira Test",
            "commit",
            "-q",
            "-m",
            "initial commit",
        ]
    )
    return path


@pytest.fixture
def ledger(tmp_path):
    k = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    yield k
    k.close()


@pytest.fixture
def lifecycle(ledger):
    return LifecycleKernel(ledger, projection=ProjectionEngine(ledger))


@pytest.fixture
def inbox(ledger):
    return InboxKernel(ledger)


@pytest.fixture
def claims(ledger):
    return ClaimKernel(ledger)


@pytest.fixture
def clean_repo(tmp_path):
    return _init_repo(tmp_path / "repo")


def _submit(inbox, payload=None):
    result = inbox.submit_intent("worker", payload or {"message": "hi"})
    assert result.success
    return result.intent_id


def _projection_row(ledger, snapshot_id):
    return ledger.connection.execute(
        "SELECT snapshot_id, intent_id, repo_path, head_sha, branch, is_dirty, "
        "status_porcelain, created_at, error_type, stderr, last_event_id "
        "FROM provenance_projection WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()


def _event_payload(ledger, event_type):
    import json

    row = ledger.connection.execute(
        "SELECT payload_json FROM ledger_events WHERE event_type = ? ORDER BY rowid DESC LIMIT 1",
        (event_type,),
    ).fetchone()
    return json.loads(row[0])


class CountingWorker:
    name = "CountingWorker"

    def __init__(self):
        self.calls = 0

    def invoke(self, inputs):
        self.calls += 1
        return WorkerResult(success=True, outputs={"calls": self.calls})


def test_provenance_capture_succeeds(ledger, clean_repo):
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    assert isinstance(snapshot, ProvenanceSnapshot)
    assert snapshot.error_type is None
    assert _projection_row(ledger, snapshot.snapshot_id) is not None


def test_failed_provenance_capture_is_recorded(ledger, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    snapshot = capture_provenance(ledger, "intent-1", str(plain))
    assert snapshot.error_type == "NOT_REPOSITORY"
    payload = _event_payload(ledger, "provenance_capture_failed")
    assert payload["content"]["snapshot_id"] == snapshot.snapshot_id
    assert payload["content"]["error_type"] == "NOT_REPOSITORY"


def test_failed_provenance_capture_prevents_worker_execution(ledger, lifecycle, inbox, tmp_path):
    intent_id = _submit(inbox)
    plain = tmp_path / "plain"
    plain.mkdir()
    worker = CountingWorker()
    result = dispatch_with_provenance(ledger, lifecycle, intent_id, worker, str(plain))
    assert not result.success
    assert result.error_type == "NOT_REPOSITORY"
    assert worker.calls == 0


def test_failed_provenance_capture_marks_intent_failed(ledger, lifecycle, inbox, tmp_path):
    intent_id = _submit(inbox)
    plain = tmp_path / "plain"
    plain.mkdir()
    dispatch_with_provenance(ledger, lifecycle, intent_id, EchoWorker(), str(plain))
    assert get_intent_status(ledger, intent_id) == "FAILED"


def test_claim_released_after_failed_provenance_capture(ledger, lifecycle, inbox, claims, tmp_path):
    intent_id = _submit(inbox)
    plain = tmp_path / "plain"
    plain.mkdir()
    dispatch_with_provenance_and_track(
        ledger, lifecycle, claims, intent_id, "dispatcher-1", EchoWorker(), str(plain)
    )
    assert get_claim(ledger, intent_id) is None


def test_head_sha_preserved(ledger, clean_repo):
    expected = _run(["git", "-C", str(clean_repo), "rev-parse", "HEAD"]).stdout.strip()
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    assert snapshot.head_sha == expected
    assert get_provenance(ledger, snapshot.snapshot_id).head_sha == expected


def test_branch_preserved(ledger, clean_repo):
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    assert snapshot.branch == "leira-test"


def test_dirty_flag_preserved(ledger, clean_repo):
    (clean_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    assert snapshot.is_dirty is True


def test_status_porcelain_preserved_exactly(ledger, clean_repo):
    (clean_repo / "exact.txt").write_text("x\n", encoding="utf-8")
    expected = _run(["git", "-C", str(clean_repo), "status", "--porcelain"]).stdout
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    assert snapshot.status_porcelain == expected


def test_dirty_repositories_are_allowed(ledger, clean_repo):
    (clean_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    assert snapshot.error_type is None
    assert snapshot.is_dirty is True


def test_clean_repositories_are_allowed(ledger, clean_repo):
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    assert snapshot.error_type is None
    assert snapshot.is_dirty is False


def test_snapshot_immutable(ledger, clean_repo):
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    with pytest.raises(Exception):
        snapshot.branch = "other"


def test_projection_rebuilt_correctly(ledger, clean_repo):
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    before = _projection_row(ledger, snapshot.snapshot_id)
    ledger.connection.execute("DELETE FROM provenance_projection")
    ledger.connection.commit()
    rebuild_projection(ledger)
    assert _projection_row(ledger, snapshot.snapshot_id) == before


def test_failed_captures_included_in_projection(ledger, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    snapshot = capture_provenance(ledger, "intent-1", str(plain))
    row = _projection_row(ledger, snapshot.snapshot_id)
    assert row is not None
    assert row[8] == "NOT_REPOSITORY"


def test_created_at_derived_from_ledger_timestamp(ledger, clean_repo):
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    created_at = ledger.connection.execute(
        "SELECT created_at FROM ledger_events WHERE event_type = 'provenance_captured'"
    ).fetchone()[0]
    assert snapshot.created_at == created_at
    assert _projection_row(ledger, snapshot.snapshot_id)[7] == created_at


def test_receipts_reference_provenance_snapshots(ledger, lifecycle, inbox, clean_repo):
    intent_id = _submit(inbox)
    result = dispatch_with_provenance(ledger, lifecycle, intent_id, EchoWorker(), str(clean_repo))
    bundle = get_receipt_bundle(ledger, intent_id)
    assert bundle is not None
    assert [p.snapshot_id for p in bundle.provenance] == [result.provenance_snapshot_id]


def test_snapshot_ids_unique(ledger, clean_repo):
    ids = {
        capture_provenance(ledger, f"intent-{i}", str(clean_repo)).snapshot_id
        for i in range(10)
    }
    assert len(ids) == 10


def test_audit_validates_projection(ledger, clean_repo):
    capture_provenance(ledger, "intent-1", str(clean_repo))
    assert audit(ledger).success


def test_audit_validates_failed_captures(ledger, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    snapshot = capture_provenance(ledger, "intent-1", str(plain))
    ledger.connection.execute(
        "UPDATE provenance_projection SET error_type = NULL WHERE snapshot_id = ?",
        (snapshot.snapshot_id,),
    )
    ledger.connection.commit()
    result = audit(ledger)
    assert not result.success
    assert f"PROVENANCE_PROJECTION_MISMATCH:{snapshot.snapshot_id}" in result.errors


def test_audit_remains_read_only(ledger, clean_repo):
    capture_provenance(ledger, "intent-1", str(clean_repo))
    before_events = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    before_projection = ledger.connection.execute(
        "SELECT * FROM provenance_projection ORDER BY snapshot_id"
    ).fetchall()
    audit(ledger)
    after_events = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    after_projection = ledger.connection.execute(
        "SELECT * FROM provenance_projection ORDER BY snapshot_id"
    ).fetchall()
    assert before_events == after_events
    assert before_projection == after_projection


def test_audit_does_not_inspect_current_repository(ledger, clean_repo, monkeypatch):
    capture_provenance(ledger, "intent-1", str(clean_repo))

    def explode(*args, **kwargs):
        raise AssertionError("audit must not inspect current git state")

    monkeypatch.setattr(provenance_module, "inspect_repo", explode)
    assert "inspect_repo" not in inspect.getsource(auditor_module)
    assert audit(ledger).success


def test_rebuild_deterministic(ledger, clean_repo):
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    rebuild_projection(ledger)
    first = _projection_row(ledger, snapshot.snapshot_id)
    rebuild_projection(ledger)
    second = _projection_row(ledger, snapshot.snapshot_id)
    assert first == second


def test_rebuild_idempotent(ledger, clean_repo):
    capture_provenance(ledger, "intent-1", str(clean_repo))
    rebuild_projection(ledger)
    first = ledger.connection.execute("SELECT * FROM provenance_projection").fetchall()
    rebuild_projection(ledger)
    second = ledger.connection.execute("SELECT * FROM provenance_projection").fetchall()
    assert first == second


def test_validate_chain_still_succeeds(ledger, clean_repo):
    capture_provenance(ledger, "intent-1", str(clean_repo))
    assert ledger.validate_chain().success


def test_repository_changes_after_capture_do_not_invalidate_history(ledger, clean_repo):
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    (clean_repo / "later.txt").write_text("later\n", encoding="utf-8")
    assert audit(ledger).success
    assert get_provenance(ledger, snapshot.snapshot_id) == snapshot


def test_current_branch_changes_after_capture_do_not_invalidate_history(ledger, clean_repo):
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    _run(["git", "-C", str(clean_repo), "checkout", "-q", "-b", "later-branch"])
    assert audit(ledger).success
    assert get_provenance(ledger, snapshot.snapshot_id).branch == snapshot.branch


def test_repository_deletion_after_capture_does_not_invalidate_audit(ledger, clean_repo):
    snapshot = capture_provenance(ledger, "intent-1", str(clean_repo))
    for path in sorted(clean_repo.rglob("*"), reverse=True):
        if path.is_file() or path.is_symlink():
            path.unlink()
        else:
            path.rmdir()
    clean_repo.rmdir()
    assert audit(ledger).success
    assert get_provenance(ledger, snapshot.snapshot_id) == snapshot


def test_provenance_uses_existing_git_adapter():
    source = inspect.getsource(provenance_module.capture_provenance)
    assert "inspect_repo(" in source


def test_no_duplicated_subprocess_logic():
    source = inspect.getsource(provenance_module)
    assert "subprocess" not in source
    assert "run_command" not in source


def test_hundred_snapshot_stress_test(ledger, lifecycle, inbox, tmp_path, clean_repo):
    dirty_repo = _init_repo(tmp_path / "dirty_repo")
    (dirty_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    plain = tmp_path / "plain"
    plain.mkdir()

    snapshot_ids = []
    for i in range(100):
        intent_id = _submit(inbox, {"n": i})
        repo = clean_repo if i % 3 == 0 else dirty_repo if i % 3 == 1 else plain
        result = dispatch_with_provenance(
            ledger, lifecycle, intent_id, EchoWorker(), str(repo)
        )
        assert result.provenance_snapshot_id is not None
        snapshot_ids.append(result.provenance_snapshot_id)

    assert len(set(snapshot_ids)) == 100
    rebuild_projection(ledger)
    assert audit(ledger).success
    for snapshot_id in snapshot_ids:
        assert GitProvenance(ledger).get_provenance(snapshot_id) is not None
