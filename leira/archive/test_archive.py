import json
import shutil
import subprocess
from pathlib import Path

import pytest

from leira.archive.archive import ArchiveBundle, ArchiveError, export_archive, import_archive
from leira.archive.replay import replay_history
from leira.audit.auditor import audit
from leira.dispatcher.dispatcher import dispatch_with_provenance
from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.inbox.inbox import InboxKernel
from leira.projection.rebuild import rebuild_projection
from leira.projection.state import ProjectionEngine
from leira.provenance.git_provenance import GitProvenance
from leira.receipts.receipts import export_receipt_bundle
from leira.sessions.sessions import SessionKernel
from leira.workspace.hashing import sha256
from leira.workspace.workspace import Workspace
from leira.workers.base import EchoWorker


def _run(args):
    return subprocess.run(args, capture_output=True, text=True, check=True)


def _init_repo(path: Path) -> Path:
    _run(["git", "init", "-q", str(path)])
    _run(["git", "-C", str(path), "checkout", "-q", "-b", "archive-test"])
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


def _machine(tmp_path, count=3, sessions_count=1):
    tmp_path.mkdir(parents=True, exist_ok=True)
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    inbox = InboxKernel(ledger)
    lifecycle = LifecycleKernel(ledger, projection=ProjectionEngine(ledger))
    workspace_root = tmp_path / "workspace"
    workspace = Workspace(ledger, workspace_root)
    sessions = SessionKernel(ledger)
    repo = _init_repo(tmp_path / "repo")
    session_ids = [sessions.create_session().session_id for _ in range(sessions_count)]
    intent_ids = []
    artifacts = []
    snapshots = []
    for i in range(count):
        submitted = inbox.submit_intent("worker", {"n": i})
        assert submitted.success
        intent_id = submitted.intent_id
        intent_ids.append(intent_id)
        artifacts.append(
            workspace.write_artifact(intent_id, f"nested/artifact-{i}.txt", f"value-{i}".encode())
        )
        result = dispatch_with_provenance(ledger, lifecycle, intent_id, EchoWorker(), str(repo))
        assert result.success
        snapshots.append(GitProvenance(ledger).get_provenance(result.provenance_snapshot_id))
        sessions.add_intent_to_session(session_ids[i % sessions_count], intent_id)
    rebuild_projection(ledger)
    replay_history(ledger, workspace_root)
    return ledger, workspace_root, intent_ids, artifacts, snapshots, session_ids


def _new_ledger(path: Path) -> LedgerKernel:
    path.mkdir(parents=True, exist_ok=True)
    return LedgerKernel(str(path / "ledger.sqlite3"))


def _manifest(archive_path: Path):
    return json.loads((archive_path / "manifest.json").read_text(encoding="utf-8"))


def _ledger_lines(archive_path: Path):
    return (archive_path / "ledger_events.jsonl").read_text(encoding="utf-8").splitlines()


def _workspace_files(root: Path):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(p for p in root.rglob("*") if p.is_file())
    }


def test_archive_export_succeeds(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    bundle = export_archive(ledger, workspace_root, tmp_path / "archive")
    assert isinstance(bundle, ArchiveBundle)
    assert (tmp_path / "archive" / "manifest.json").exists()
    ledger.close()


def test_archive_import_succeeds(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    exported = export_archive(ledger, workspace_root, archive)
    target = _new_ledger(tmp_path / "dst")
    imported = import_archive(target, tmp_path / "dst_workspace", archive)
    assert imported.event_count == exported.event_count
    ledger.close()
    target.close()


def test_replay_succeeds_from_empty_database(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    assert replay_history(target, tmp_path / "dst_workspace").success
    ledger.close()
    target.close()


def test_ledger_jsonl_preserves_ledger_order(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    expected = [
        row[0]
        for row in ledger.connection.execute("SELECT id FROM ledger_events ORDER BY rowid").fetchall()
    ]
    actual = [json.loads(line)["id"] for line in _ledger_lines(archive)]
    assert actual == expected
    ledger.close()


def test_event_count_preserved(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    bundle = export_archive(ledger, workspace_root, archive)
    assert _manifest(archive)["event_count"] == bundle.event_count
    ledger.close()


def test_first_event_id_preserved(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    bundle = export_archive(ledger, workspace_root, archive)
    assert _manifest(archive)["first_event_id"] == bundle.first_event_id
    ledger.close()


def test_last_event_id_preserved(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    bundle = export_archive(ledger, workspace_root, archive)
    assert _manifest(archive)["last_event_id"] == bundle.last_event_id
    ledger.close()


def test_ledger_events_sha256_verified(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    manifest = _manifest(archive)
    assert sha256((archive / "ledger_events.jsonl").read_bytes()) == manifest["ledger_events_sha256"]
    ledger.close()


def test_workspace_files_copied_exactly(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    assert _workspace_files(workspace_root) == _workspace_files(archive / "workspace")
    ledger.close()


def test_artifact_relative_paths_preserved(tmp_path):
    ledger, workspace_root, _ids, artifacts, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    for descriptor in artifacts:
        path = archive / "workspace" / "intents" / descriptor.intent_id / "artifacts" / descriptor.relative_path
        assert path.exists()
    ledger.close()


def test_imported_artifact_hashes_verified(tmp_path):
    ledger, workspace_root, _ids, artifacts, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    target = _new_ledger(tmp_path / "dst")
    target_workspace = tmp_path / "dst_workspace"
    import_archive(target, target_workspace, archive)
    for descriptor in artifacts:
        path = target_workspace / "intents" / descriptor.intent_id / "artifacts" / descriptor.relative_path
        assert sha256(path.read_bytes()) == descriptor.sha256
    ledger.close()
    target.close()


def test_export_fails_on_corrupted_artifact_file(tmp_path):
    ledger, workspace_root, _ids, artifacts, *_ = _machine(tmp_path / "src")
    descriptor = artifacts[0]
    path = workspace_root / "intents" / descriptor.intent_id / "artifacts" / descriptor.relative_path
    path.write_bytes(b"corrupt")
    with pytest.raises(ArchiveError) as exc:
        export_archive(ledger, workspace_root, tmp_path / "archive")
    assert exc.value.error_type in {"SIZE_MISMATCH", "HASH_MISMATCH"}
    ledger.close()


def test_import_fails_on_corrupted_archive_artifact(tmp_path):
    ledger, workspace_root, _ids, artifacts, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    descriptor = artifacts[0]
    path = archive / "workspace" / "intents" / descriptor.intent_id / "artifacts" / descriptor.relative_path
    path.write_bytes(b"corrupt")
    target = _new_ledger(tmp_path / "dst")
    with pytest.raises(ArchiveError):
        import_archive(target, tmp_path / "dst_workspace", archive)
    ledger.close()
    target.close()


def test_projections_rebuilt_after_replay(tmp_path):
    ledger, workspace_root, _ids, _artifacts, _snapshots, session_ids = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    assert target.connection.execute("SELECT COUNT(*) FROM session_projection").fetchone()[0] == len(session_ids)
    assert target.connection.execute("SELECT COUNT(*) FROM artifact_projection").fetchone()[0] > 0
    ledger.close()
    target.close()


def test_receipts_preserved(tmp_path):
    ledger, workspace_root, intent_ids, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    before = {intent_id: export_receipt_bundle(ledger, intent_id) for intent_id in intent_ids}
    export_archive(ledger, workspace_root, archive)
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    after = {intent_id: export_receipt_bundle(target, intent_id) for intent_id in intent_ids}
    assert after == before
    ledger.close()
    target.close()


def test_session_memberships_preserved(tmp_path):
    ledger, workspace_root, _ids, _artifacts, _snapshots, session_ids = _machine(tmp_path / "src", count=4, sessions_count=2)
    archive = tmp_path / "archive"
    before = {
        session_id: SessionKernel(ledger).list_session_intents(session_id)
        for session_id in session_ids
    }
    export_archive(ledger, workspace_root, archive)
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    after = {
        session_id: SessionKernel(target).list_session_intents(session_id)
        for session_id in session_ids
    }
    assert after == before
    ledger.close()
    target.close()


def test_provenance_snapshots_preserved(tmp_path):
    ledger, workspace_root, _ids, _artifacts, snapshots, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    for snapshot in snapshots:
        assert GitProvenance(target).get_provenance(snapshot.snapshot_id) == snapshot
    ledger.close()
    target.close()


def test_deterministic_ledger_export(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    export_archive(ledger, workspace_root, tmp_path / "a1", created_at="fixed")
    export_archive(ledger, workspace_root, tmp_path / "a2", created_at="fixed")
    assert (tmp_path / "a1" / "ledger_events.jsonl").read_bytes() == (
        tmp_path / "a2" / "ledger_events.jsonl"
    ).read_bytes()
    ledger.close()


def test_repeated_export_byte_identical_with_fixed_created_at(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    export_archive(ledger, workspace_root, tmp_path / "a1", created_at="fixed")
    export_archive(ledger, workspace_root, tmp_path / "a2", created_at="fixed")
    assert _workspace_files(tmp_path / "a1") == _workspace_files(tmp_path / "a2")
    ledger.close()


def test_replay_deterministic(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    target_a = _new_ledger(tmp_path / "dst_a")
    target_b = _new_ledger(tmp_path / "dst_b")
    import_archive(target_a, tmp_path / "wa", archive)
    import_archive(target_b, tmp_path / "wb", archive)
    rows_a = target_a.connection.execute("SELECT * FROM artifact_projection ORDER BY artifact_id").fetchall()
    rows_b = target_b.connection.execute("SELECT * FROM artifact_projection ORDER BY artifact_id").fetchall()
    assert rows_a == rows_b
    ledger.close()
    target_a.close()
    target_b.close()


def test_replay_does_not_execute_workers(tmp_path, monkeypatch):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    import leira.workers.base as workers_module

    monkeypatch.setattr(workers_module, "invoke_worker", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    ledger.close()
    target.close()


def test_replay_does_not_invoke_dispatcher(tmp_path, monkeypatch):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    import leira.dispatcher.dispatcher as dispatcher_module

    monkeypatch.setattr(dispatcher_module, "dispatch_with_provenance", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    ledger.close()
    target.close()


def test_replay_does_not_invoke_shell_adapter(tmp_path, monkeypatch):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    import leira.dispatcher.shell as shell_module

    monkeypatch.setattr(shell_module, "run_command", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    ledger.close()
    target.close()


def test_replay_does_not_inspect_git(tmp_path, monkeypatch):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    import leira.provenance.git_provenance as provenance_module

    monkeypatch.setattr(provenance_module, "inspect_repo", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    ledger.close()
    target.close()


def test_replay_does_not_regenerate_artifacts(tmp_path):
    ledger, workspace_root, _ids, artifacts, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    assert target.connection.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0] == ledger.connection.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0]
    assert len(_workspace_files(tmp_path / "dst_workspace")) == len(artifacts)
    ledger.close()
    target.close()


def test_replay_does_not_repair_corrupted_history(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    lines = _ledger_lines(archive)
    event = json.loads(lines[0])
    event["event_hash"] = "0" * 64
    lines[0] = json.dumps(event, sort_keys=True, separators=(",", ":"))
    content = ("\n".join(lines) + "\n").encode("utf-8")
    (archive / "ledger_events.jsonl").write_bytes(content)
    manifest = _manifest(archive)
    manifest["ledger_events_sha256"] = sha256(content)
    (archive / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    target = _new_ledger(tmp_path / "dst")
    with pytest.raises(ArchiveError) as exc:
        import_archive(target, tmp_path / "dst_workspace", archive)
    assert exc.value.error_type == "BROKEN_HASH_CHAIN"
    ledger.close()
    target.close()


def test_validate_chain_succeeds_after_replay(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    assert target.validate_chain().success
    ledger.close()
    target.close()


def test_audit_validates_replay(tmp_path):
    ledger, workspace_root, *_ = _machine(tmp_path / "src")
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    assert audit(target, tmp_path / "dst_workspace").success
    ledger.close()
    target.close()


def test_replay_uses_ledger_timestamps_only(tmp_path):
    import inspect
    import leira.archive.replay as replay_module

    source = inspect.getsource(replay_module)
    assert "datetime.now" not in source
    assert "timezone" not in source


def test_hundred_intent_archive_replay_stress(tmp_path):
    ledger, workspace_root, intent_ids, artifacts, snapshots, session_ids = _machine(
        tmp_path / "src", count=100, sessions_count=10
    )
    before_receipts = {intent_id: export_receipt_bundle(ledger, intent_id) for intent_id in intent_ids}
    before_sessions = {
        session_id: SessionKernel(ledger).list_session_intents(session_id)
        for session_id in session_ids
    }
    archive = tmp_path / "archive"
    export_archive(ledger, workspace_root, archive)
    shutil.rmtree(workspace_root)
    ledger.close()

    target = _new_ledger(tmp_path / "dst")
    import_archive(target, tmp_path / "dst_workspace", archive)
    assert target.validate_chain().success
    assert audit(target, tmp_path / "dst_workspace").success
    assert {intent_id: export_receipt_bundle(target, intent_id) for intent_id in intent_ids} == before_receipts
    assert {
        session_id: SessionKernel(target).list_session_intents(session_id)
        for session_id in session_ids
    } == before_sessions
    for descriptor in artifacts:
        path = tmp_path / "dst_workspace" / "intents" / descriptor.intent_id / "artifacts" / descriptor.relative_path
        assert sha256(path.read_bytes()) == descriptor.sha256
    for snapshot in snapshots:
        assert GitProvenance(target).get_provenance(snapshot.snapshot_id) == snapshot
    target.close()
