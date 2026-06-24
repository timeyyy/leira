import inspect
import subprocess
from pathlib import Path

import pytest

from leira.audit.auditor import audit
from leira.dispatcher.dispatcher import dispatch_with_provenance
from leira.dispatcher.kernel import LedgerKernel
from leira.dispatcher.lifecycle import LifecycleKernel
from leira.inbox.inbox import InboxKernel
from leira.projection.rebuild import rebuild_projection
from leira.projection.state import ProjectionEngine
from leira.provenance.git_provenance import capture_provenance
from leira.sessions import sessions as sessions_module
from leira.sessions.sessions import SessionBundle, SessionKernel, rebuild_session_projection
from leira.workspace.workspace import Workspace
from leira.workers.base import EchoWorker


def _run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(path: Path) -> Path:
    _run(["git", "init", "-q", str(path)])
    _run(["git", "-C", str(path), "checkout", "-q", "-b", "session-test"])
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
def sessions(ledger):
    return SessionKernel(ledger)


@pytest.fixture
def workspace(tmp_path, ledger):
    return Workspace(ledger, tmp_path / "workspace")


@pytest.fixture
def repo(tmp_path):
    return _init_repo(tmp_path / "repo")


def _submit(inbox, payload=None):
    result = inbox.submit_intent("worker", payload or {"message": "hi"})
    assert result.success
    return result.intent_id


def _event_count(ledger, event_type):
    return ledger.connection.execute(
        "SELECT COUNT(*) FROM ledger_events WHERE event_type = ?", (event_type,)
    ).fetchone()[0]


def _membership_rows(ledger, session_id):
    return ledger.connection.execute(
        "SELECT session_id, intent_id, membership_order, created_at, last_event_id "
        "FROM session_membership_projection WHERE session_id = ? ORDER BY membership_order",
        (session_id,),
    ).fetchall()


def test_session_creation_succeeds(sessions):
    bundle = sessions.create_session()
    assert isinstance(bundle, SessionBundle)
    assert bundle.intent_ids == []


def test_intent_addition_succeeds(sessions, inbox):
    session = sessions.create_session()
    intent_id = _submit(inbox)
    result = sessions.add_intent_to_session(session.session_id, intent_id)
    assert result.success
    assert sessions.list_session_intents(session.session_id) == [intent_id]


def test_duplicate_membership_rejected(sessions, inbox):
    session = sessions.create_session()
    intent_id = _submit(inbox)
    sessions.add_intent_to_session(session.session_id, intent_id)
    result = sessions.add_intent_to_session(session.session_id, intent_id)
    assert not result.success
    assert result.error_type == "DUPLICATE_MEMBERSHIP"


def test_unknown_session_rejected(sessions, inbox):
    intent_id = _submit(inbox)
    result = sessions.add_intent_to_session("missing-session", intent_id)
    assert not result.success
    assert result.error_type == "UNKNOWN_SESSION"


def test_unknown_intent_rejected(sessions):
    session = sessions.create_session()
    result = sessions.add_intent_to_session(session.session_id, "missing-intent")
    assert not result.success
    assert result.error_type == "UNKNOWN_INTENT"


def test_session_created_event_recorded(ledger, sessions):
    sessions.create_session()
    assert _event_count(ledger, "session_created") == 1


def test_session_intent_added_recorded(ledger, sessions, inbox):
    session = sessions.create_session()
    sessions.add_intent_to_session(session.session_id, _submit(inbox))
    assert _event_count(ledger, "session_intent_added") == 1


def test_session_intent_rejected_recorded(ledger, sessions):
    session = sessions.create_session()
    sessions.add_intent_to_session(session.session_id, "missing-intent")
    assert _event_count(ledger, "session_intent_rejected") == 1


def test_session_membership_order_preserved(sessions, inbox):
    session = sessions.create_session()
    ids = [_submit(inbox, {"n": i}) for i in range(3)]
    for intent_id in ids:
        sessions.add_intent_to_session(session.session_id, intent_id)
    assert sessions.list_session_intents(session.session_id) == ids


def test_membership_order_increments_per_session_id(ledger, sessions, inbox):
    a = sessions.create_session()
    b = sessions.create_session()
    ids = [_submit(inbox, {"n": i}) for i in range(4)]
    sessions.add_intent_to_session(a.session_id, ids[0])
    sessions.add_intent_to_session(b.session_id, ids[1])
    sessions.add_intent_to_session(a.session_id, ids[2])
    sessions.add_intent_to_session(b.session_id, ids[3])
    assert [row[2] for row in _membership_rows(ledger, a.session_id)] == [1, 2]
    assert [row[2] for row in _membership_rows(ledger, b.session_id)] == [1, 2]


def test_membership_order_derives_from_ledger_order(ledger, sessions, inbox):
    session = sessions.create_session()
    first = _submit(inbox, {"n": 1})
    second = _submit(inbox, {"n": 2})
    sessions.add_intent_to_session(session.session_id, first)
    sessions.add_intent_to_session(session.session_id, second)
    rows = _membership_rows(ledger, session.session_id)
    event_rowids = [
        ledger.connection.execute(
            "SELECT rowid FROM ledger_events WHERE id = ?", (row[4],)
        ).fetchone()[0]
        for row in rows
    ]
    assert event_rowids == sorted(event_rowids)
    assert [row[2] for row in rows] == [1, 2]


def test_receipt_retrieval_succeeds(ledger, lifecycle, sessions, inbox, repo):
    session = sessions.create_session()
    intent_id = _submit(inbox)
    dispatch_with_provenance(ledger, lifecycle, intent_id, EchoWorker(), str(repo))
    sessions.add_intent_to_session(session.session_id, intent_id)
    receipts = sessions.get_session_receipts(session.session_id)
    assert [bundle.intent_id for bundle in receipts] == [intent_id]


def test_receipt_retrieval_uses_membership_order(ledger, lifecycle, sessions, inbox, repo):
    session = sessions.create_session()
    first = _submit(inbox, {"n": 1})
    second = _submit(inbox, {"n": 2})
    dispatch_with_provenance(ledger, lifecycle, second, EchoWorker(), str(repo))
    dispatch_with_provenance(ledger, lifecycle, first, EchoWorker(), str(repo))
    sessions.add_intent_to_session(session.session_id, first)
    sessions.add_intent_to_session(session.session_id, second)
    assert [bundle.intent_id for bundle in sessions.get_session_receipts(session.session_id)] == [
        first,
        second,
    ]


def test_artifact_retrieval_succeeds(sessions, inbox, workspace):
    session = sessions.create_session()
    intent_id = _submit(inbox)
    descriptor = workspace.write_artifact(intent_id, "output.txt", b"hello")
    sessions.add_intent_to_session(session.session_id, intent_id)
    assert sessions.get_session_artifacts(session.session_id) == [descriptor]


def test_artifact_retrieval_uses_membership_order(sessions, inbox, workspace):
    session = sessions.create_session()
    first = _submit(inbox, {"n": 1})
    second = _submit(inbox, {"n": 2})
    second_artifact = workspace.write_artifact(second, "output.txt", b"second")
    first_artifact = workspace.write_artifact(first, "output.txt", b"first")
    sessions.add_intent_to_session(session.session_id, first)
    sessions.add_intent_to_session(session.session_id, second)
    assert sessions.get_session_artifacts(session.session_id) == [first_artifact, second_artifact]


def test_provenance_retrieval_succeeds(ledger, sessions, inbox, repo):
    session = sessions.create_session()
    intent_id = _submit(inbox)
    snapshot = capture_provenance(ledger, intent_id, str(repo))
    sessions.add_intent_to_session(session.session_id, intent_id)
    assert sessions.get_session_provenance(session.session_id) == [snapshot]


def test_provenance_retrieval_uses_membership_order(ledger, sessions, inbox, repo):
    session = sessions.create_session()
    first = _submit(inbox, {"n": 1})
    second = _submit(inbox, {"n": 2})
    second_snapshot = capture_provenance(ledger, second, str(repo))
    first_snapshot = capture_provenance(ledger, first, str(repo))
    sessions.add_intent_to_session(session.session_id, first)
    sessions.add_intent_to_session(session.session_id, second)
    assert sessions.get_session_provenance(session.session_id) == [
        first_snapshot,
        second_snapshot,
    ]


def test_retrieval_methods_do_not_execute_extra_logic():
    source = "\n".join(
        inspect.getsource(fn)
        for fn in (
            sessions_module.get_session_receipts,
            sessions_module.get_session_artifacts,
            sessions_module.get_session_provenance,
        )
    )
    forbidden = ("dispatch", "invoke", "inspect_repo", "read_bytes", "sha256(")
    for word in forbidden:
        assert word not in source


def test_projection_rebuilt_correctly(ledger, sessions, inbox):
    session = sessions.create_session()
    first = _submit(inbox)
    second = _submit(inbox)
    sessions.add_intent_to_session(session.session_id, first)
    sessions.add_intent_to_session(session.session_id, second)
    before = _membership_rows(ledger, session.session_id)
    ledger.connection.execute("DELETE FROM session_membership_projection")
    ledger.connection.execute("DELETE FROM session_projection")
    ledger.connection.commit()
    rebuild_projection(ledger)
    assert _membership_rows(ledger, session.session_id) == before


def test_created_at_derived_from_ledger_timestamps(ledger, sessions):
    session = sessions.create_session()
    created_at = ledger.connection.execute(
        "SELECT created_at FROM ledger_events WHERE event_type = 'session_created'"
    ).fetchone()[0]
    row = ledger.connection.execute(
        "SELECT created_at FROM session_projection WHERE session_id = ?",
        (session.session_id,),
    ).fetchone()
    assert session.created_at == created_at
    assert row[0] == created_at


def test_rebuild_deterministic(ledger, sessions, inbox):
    session = sessions.create_session()
    sessions.add_intent_to_session(session.session_id, _submit(inbox))
    rebuild_session_projection(ledger)
    first = _membership_rows(ledger, session.session_id)
    rebuild_session_projection(ledger)
    second = _membership_rows(ledger, session.session_id)
    assert first == second


def test_rebuild_idempotent(ledger, sessions, inbox):
    session = sessions.create_session()
    sessions.add_intent_to_session(session.session_id, _submit(inbox))
    rebuild_session_projection(ledger)
    first = ledger.connection.execute("SELECT * FROM session_projection").fetchall()
    rebuild_session_projection(ledger)
    second = ledger.connection.execute("SELECT * FROM session_projection").fetchall()
    assert first == second


def test_projection_loss_recoverable(ledger, sessions, inbox):
    session = sessions.create_session()
    intent_id = _submit(inbox)
    sessions.add_intent_to_session(session.session_id, intent_id)
    ledger.connection.execute("DROP TABLE session_membership_projection")
    ledger.connection.execute("DROP TABLE session_projection")
    ledger.connection.commit()
    rebuild_projection(ledger)
    assert sessions.get_session(session.session_id).intent_ids == [intent_id]


def test_audit_validates_sessions(ledger, sessions, inbox):
    session = sessions.create_session()
    sessions.add_intent_to_session(session.session_id, _submit(inbox))
    assert audit(ledger).success


def test_audit_detects_duplicate_memberships(ledger, sessions, inbox):
    session = sessions.create_session()
    intent_id = _submit(inbox)
    sessions.add_intent_to_session(session.session_id, intent_id)
    result = ledger.append_event(
        event_type="session_intent_added",
        worker_id="kernel",
        payload={
            "session_id": session.session_id,
            "intent_id": intent_id,
            "artifact": {
                "type": "session_membership",
                "content": {
                    "session_id": session.session_id,
                    "intent_id": intent_id,
                    "error_type": None,
                },
            },
        },
    )
    assert result.success
    audit_result = audit(ledger)
    assert not audit_result.success
    assert any(e.startswith("DUPLICATE_SESSION_MEMBERSHIP:") for e in audit_result.errors)


def test_audit_detects_unknown_session_references(ledger, inbox):
    intent_id = _submit(inbox)
    result = ledger.append_event(
        event_type="session_intent_added",
        worker_id="kernel",
        payload={"session_id": "missing-session", "intent_id": intent_id},
    )
    assert result.success
    audit_result = audit(ledger)
    assert not audit_result.success
    assert f"SESSION_UNKNOWN_SESSION:{result.event_id}" in audit_result.errors


def test_audit_detects_unknown_intent_references(ledger, sessions):
    session = sessions.create_session()
    result = ledger.append_event(
        event_type="session_intent_added",
        worker_id="kernel",
        payload={"session_id": session.session_id, "intent_id": "missing-intent"},
    )
    assert result.success
    audit_result = audit(ledger)
    assert not audit_result.success
    assert f"SESSION_UNKNOWN_INTENT:{result.event_id}" in audit_result.errors


def test_audit_remains_read_only(ledger, sessions, inbox):
    session = sessions.create_session()
    sessions.add_intent_to_session(session.session_id, _submit(inbox))
    before_events = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    before_sessions = ledger.connection.execute("SELECT * FROM session_projection").fetchall()
    before_memberships = ledger.connection.execute(
        "SELECT * FROM session_membership_projection"
    ).fetchall()
    audit(ledger)
    after_events = ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()
    after_sessions = ledger.connection.execute("SELECT * FROM session_projection").fetchall()
    after_memberships = ledger.connection.execute(
        "SELECT * FROM session_membership_projection"
    ).fetchall()
    assert before_events == after_events
    assert before_sessions == after_sessions
    assert before_memberships == after_memberships


def test_validate_chain_still_succeeds(ledger, sessions, inbox):
    session = sessions.create_session()
    sessions.add_intent_to_session(session.session_id, _submit(inbox))
    assert ledger.validate_chain().success


def test_session_grouping_does_not_imply_execution_order(ledger, sessions, inbox, lifecycle, repo):
    session = sessions.create_session()
    first = _submit(inbox, {"n": 1})
    second = _submit(inbox, {"n": 2})
    dispatch_with_provenance(ledger, lifecycle, second, EchoWorker(), str(repo))
    sessions.add_intent_to_session(session.session_id, first)
    sessions.add_intent_to_session(session.session_id, second)
    event_types = [
        row[0]
        for row in ledger.connection.execute(
            "SELECT event_type FROM ledger_events ORDER BY rowid"
        ).fetchall()
    ]
    assert event_types.index("intent_completed") < event_types.index("session_intent_added")
    assert sessions.list_session_intents(session.session_id) == [first, second]


def test_no_remove_api_exists():
    assert not hasattr(SessionKernel, "remove_intent_from_session")
    assert "remove_intent" not in inspect.getsource(sessions_module)


def test_no_reorder_api_exists():
    assert not hasattr(SessionKernel, "reorder_session")
    assert "reorder" not in inspect.getsource(sessions_module)


def test_no_dependency_graph_exists():
    source = inspect.getsource(sessions_module).lower()
    forbidden = ("dependency", "depends_on", "dag", "priority", "scheduler")
    for word in forbidden:
        assert word not in source


def test_hundred_intent_stress_test(ledger, lifecycle, sessions, inbox, workspace, repo):
    session_ids = [sessions.create_session().session_id for _ in range(10)]
    expected: dict[str, list[str]] = {session_id: [] for session_id in session_ids}

    for i in range(100):
        intent_id = _submit(inbox, {"n": i})
        session_id = session_ids[i % 10]
        expected[session_id].append(intent_id)
        workspace.write_artifact(intent_id, f"artifact-{i}.txt", f"value-{i}".encode())
        dispatch_with_provenance(ledger, lifecycle, intent_id, EchoWorker(), str(repo))
        result = sessions.add_intent_to_session(session_id, intent_id)
        assert result.success

    rebuild_projection(ledger)
    for session_id, intent_ids in expected.items():
        assert sessions.list_session_intents(session_id) == intent_ids
        assert [bundle.intent_id for bundle in sessions.get_session_receipts(session_id)] == intent_ids
        assert [artifact.intent_id for artifact in sessions.get_session_artifacts(session_id)] == intent_ids
        assert [snapshot.intent_id for snapshot in sessions.get_session_provenance(session_id)] == intent_ids
    assert audit(ledger).success
