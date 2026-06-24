import sys
import platform as platform_module
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
from leira.environment import environment as environment_module
from leira.environment.environment import (
    EnvironmentKernel,
    EnvironmentSnapshot,
    PackageInfo,
    capture_environment,
)
from leira.inbox.inbox import InboxKernel, get_intent_status
from leira.projection.rebuild import rebuild_projection
from leira.projection.state import ProjectionEngine
from leira.receipts.receipts import get_receipt_bundle
from leira.workers.base import EchoWorker, WorkerResult


def _run(args):
    return subprocess.run(args, capture_output=True, text=True, check=True)


def _init_repo(path: Path) -> Path:
    _run(["git", "init", "-q", str(path)])
    _run(["git", "-C", str(path), "checkout", "-q", "-b", "env-test"])
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
def inbox(ledger):
    return InboxKernel(ledger)


@pytest.fixture
def lifecycle(ledger):
    return LifecycleKernel(ledger, projection=ProjectionEngine(ledger))


@pytest.fixture
def claims(ledger):
    return ClaimKernel(ledger)


@pytest.fixture
def repo(tmp_path):
    return _init_repo(tmp_path / "repo")


def _submit(inbox, payload=None):
    result = inbox.submit_intent("worker", payload or {"message": "hi"})
    assert result.success
    return result.intent_id


def _projection_row(ledger, snapshot_id):
    return ledger.connection.execute(
        "SELECT snapshot_id, intent_id, python_version, platform, executable, "
        "created_at, error_type, last_event_id FROM environment_projection WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()


class CountingWorker:
    name = "CountingWorker"

    def __init__(self):
        self.calls = 0

    def invoke(self, inputs):
        self.calls += 1
        return WorkerResult(success=True, outputs={"calls": self.calls})


def test_environment_capture_succeeds(ledger):
    snapshot = capture_environment(ledger, "intent-1")
    assert isinstance(snapshot, EnvironmentSnapshot)
    assert snapshot.error_type is None
    assert snapshot.python_version == sys.version


def test_environment_capture_failure_is_recorded(ledger, monkeypatch):
    def explode():
        raise RuntimeError("boom")

    monkeypatch.setattr(environment_module, "_capture_packages", explode)
    snapshot = capture_environment(ledger, "intent-1")
    assert snapshot.error_type == "UNEXPECTED"
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM ledger_events WHERE event_type = 'environment_capture_failed'"
    ).fetchone()[0] == 1


def test_environment_capture_failure_prevents_worker_execution(ledger, lifecycle, inbox, repo, monkeypatch):
    monkeypatch.setattr(environment_module, "_capture_packages", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    intent_id = _submit(inbox)
    worker = CountingWorker()
    result = dispatch_with_provenance(ledger, lifecycle, intent_id, worker, str(repo))
    assert not result.success
    assert result.error_type == "UNEXPECTED"
    assert worker.calls == 0


def test_environment_capture_failure_marks_intent_failed(ledger, lifecycle, inbox, repo, monkeypatch):
    monkeypatch.setattr(environment_module, "_capture_packages", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    intent_id = _submit(inbox)
    dispatch_with_provenance(ledger, lifecycle, intent_id, EchoWorker(), str(repo))
    assert get_intent_status(ledger, intent_id) == "FAILED"


def test_claim_released_after_failed_environment_capture(ledger, lifecycle, inbox, claims, repo, monkeypatch):
    monkeypatch.setattr(environment_module, "_capture_packages", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    intent_id = _submit(inbox)
    dispatch_with_provenance_and_track(
        ledger, lifecycle, claims, intent_id, "dispatcher-1", EchoWorker(), str(repo)
    )
    assert get_claim(ledger, intent_id) is None


def test_python_version_preserved(ledger):
    snapshot = capture_environment(ledger, "intent-1")
    assert EnvironmentKernel(ledger).get_environment(snapshot.snapshot_id).python_version == sys.version


def test_platform_preserved(ledger):
    snapshot = capture_environment(ledger, "intent-1")
    assert snapshot.platform == platform_module.platform()


def test_executable_preserved(ledger):
    snapshot = capture_environment(ledger, "intent-1")
    assert snapshot.executable == sys.executable


def test_package_list_preserved(ledger):
    snapshot = capture_environment(ledger, "intent-1")
    loaded = EnvironmentKernel(ledger).get_environment(snapshot.snapshot_id)
    assert loaded.installed_packages == snapshot.installed_packages


def test_package_list_sorted_deterministically(ledger, monkeypatch):
    packages = [
        PackageInfo("Zoo", "1"),
        PackageInfo("alpha", "2"),
        PackageInfo("Alpha", "1"),
    ]
    monkeypatch.setattr(environment_module, "_capture_packages", lambda: sorted(packages, key=lambda p: (p.name.lower(), p.version)))
    snapshot = capture_environment(ledger, "intent-1")
    assert [(p.name, p.version) for p in snapshot.installed_packages] == [
        ("Alpha", "1"),
        ("alpha", "2"),
        ("Zoo", "1"),
    ]


def test_package_names_preserved_as_reported(ledger, monkeypatch):
    monkeypatch.setattr(
        environment_module,
        "_capture_packages",
        lambda: [PackageInfo("Mixed-Case_Name", "1.0")],
    )
    snapshot = capture_environment(ledger, "intent-1")
    assert snapshot.installed_packages[0].name == "Mixed-Case_Name"


def test_projection_rebuilt_correctly(ledger):
    snapshot = capture_environment(ledger, "intent-1")
    before = _projection_row(ledger, snapshot.snapshot_id)
    ledger.connection.execute("DELETE FROM environment_projection")
    ledger.connection.commit()
    rebuild_projection(ledger)
    assert _projection_row(ledger, snapshot.snapshot_id) == before


def test_failed_captures_included_in_projection(ledger, monkeypatch):
    monkeypatch.setattr(environment_module, "_capture_packages", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    snapshot = capture_environment(ledger, "intent-1")
    assert _projection_row(ledger, snapshot.snapshot_id)[6] == "UNEXPECTED"


def test_created_at_derived_from_ledger_timestamp(ledger):
    snapshot = capture_environment(ledger, "intent-1")
    created_at = ledger.connection.execute(
        "SELECT created_at FROM ledger_events WHERE event_type = 'environment_captured'"
    ).fetchone()[0]
    assert snapshot.created_at == created_at
    assert _projection_row(ledger, snapshot.snapshot_id)[5] == created_at


def test_receipts_reference_environment_snapshots(ledger, lifecycle, inbox, repo):
    intent_id = _submit(inbox)
    result = dispatch_with_provenance(ledger, lifecycle, intent_id, EchoWorker(), str(repo))
    bundle = get_receipt_bundle(ledger, intent_id)
    assert [snapshot.snapshot_id for snapshot in bundle.environment] == [
        result.environment_snapshot_id
    ]


def test_snapshot_ids_unique(ledger):
    ids = {capture_environment(ledger, f"intent-{i}").snapshot_id for i in range(10)}
    assert len(ids) == 10


def test_audit_validates_environment_projection(ledger):
    capture_environment(ledger, "intent-1")
    assert audit(ledger).success


def test_audit_validates_failed_captures(ledger, monkeypatch):
    monkeypatch.setattr(environment_module, "_capture_packages", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    snapshot = capture_environment(ledger, "intent-1")
    ledger.connection.execute(
        "UPDATE environment_projection SET error_type = NULL WHERE snapshot_id = ?",
        (snapshot.snapshot_id,),
    )
    ledger.connection.commit()
    result = audit(ledger)
    assert not result.success
    assert f"ENVIRONMENT_PROJECTION_MISMATCH:{snapshot.snapshot_id}" in result.errors


def test_audit_remains_read_only(ledger):
    capture_environment(ledger, "intent-1")
    before_events = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    before_projection = ledger.connection.execute("SELECT * FROM environment_projection").fetchall()
    audit(ledger)
    after_events = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    after_projection = ledger.connection.execute("SELECT * FROM environment_projection").fetchall()
    assert before_events == after_events
    assert before_projection == after_projection


def test_audit_does_not_inspect_current_environment(ledger, monkeypatch):
    capture_environment(ledger, "intent-1")
    monkeypatch.setattr(environment_module, "_capture_packages", lambda: (_ for _ in ()).throw(AssertionError()))
    assert "_capture_packages" not in __import__("inspect").getsource(auditor_module)
    assert audit(ledger).success


def test_rebuild_deterministic(ledger):
    snapshot = capture_environment(ledger, "intent-1")
    rebuild_projection(ledger)
    first = _projection_row(ledger, snapshot.snapshot_id)
    rebuild_projection(ledger)
    second = _projection_row(ledger, snapshot.snapshot_id)
    assert first == second


def test_rebuild_idempotent(ledger):
    capture_environment(ledger, "intent-1")
    rebuild_projection(ledger)
    first = ledger.connection.execute("SELECT * FROM environment_projection").fetchall()
    rebuild_projection(ledger)
    second = ledger.connection.execute("SELECT * FROM environment_projection").fetchall()
    assert first == second


def test_validate_chain_still_succeeds(ledger):
    capture_environment(ledger, "intent-1")
    assert ledger.validate_chain().success


def test_current_package_changes_after_capture_do_not_invalidate_history(ledger, monkeypatch):
    snapshot = capture_environment(ledger, "intent-1")
    monkeypatch.setattr(environment_module, "_capture_packages", lambda: [PackageInfo("Later", "9")])
    assert audit(ledger).success
    assert EnvironmentKernel(ledger).get_environment(snapshot.snapshot_id) == snapshot


def test_current_python_executable_changes_after_capture_do_not_invalidate_history(ledger, monkeypatch):
    snapshot = capture_environment(ledger, "intent-1")
    monkeypatch.setattr(environment_module.sys, "executable", "/changed/python")
    assert audit(ledger).success
    assert EnvironmentKernel(ledger).get_environment(snapshot.snapshot_id).executable == snapshot.executable


def test_oversized_package_payload_records_environment_capture_failed(ledger, monkeypatch):
    monkeypatch.setattr(environment_module, "MAX_ENVIRONMENT_PAYLOAD_BYTES", 100)
    monkeypatch.setattr(environment_module, "_capture_packages", lambda: [PackageInfo("X" * 200, "1")])
    snapshot = capture_environment(ledger, "intent-1")
    assert snapshot.error_type == "ARTIFACT_TOO_LARGE"
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM ledger_events WHERE event_type = 'environment_capture_failed'"
    ).fetchone()[0] == 1


def test_no_package_installation_occurs():
    source = __import__("inspect").getsource(environment_module)
    forbidden = ("pip install", "subprocess", "run_command")
    for word in forbidden:
        assert word not in source


def test_no_environment_creation_occurs():
    source = __import__("inspect").getsource(environment_module).lower()
    forbidden = ("venv", "virtualenv", "conda")
    for word in forbidden:
        assert word not in source


def test_no_containers_are_used():
    source = __import__("inspect").getsource(environment_module).lower()
    forbidden = ("docker", "container", "podman")
    for word in forbidden:
        assert word not in source


def test_hundred_snapshot_stress_test(ledger, lifecycle, inbox, repo):
    ids = []
    for i in range(100):
        intent_id = _submit(inbox, {"n": i})
        result = dispatch_with_provenance(ledger, lifecycle, intent_id, EchoWorker(), str(repo))
        assert result.environment_snapshot_id is not None
        ids.append(result.environment_snapshot_id)
    assert len(set(ids)) == 100
    rebuild_projection(ledger)
    assert audit(ledger).success
    for snapshot_id in ids:
        assert EnvironmentKernel(ledger).get_environment(snapshot_id) is not None
