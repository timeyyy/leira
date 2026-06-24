from pathlib import Path

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.project_state.state import build_project_state


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_count(ledger):
    return ledger.connection.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0]


def test_project_state_is_read_only(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_count(ledger)
        build_project_state(ledger, _repo_root())
        after = _event_count(ledger)
        assert after == before
    finally:
        ledger.close()


def test_project_state_can_be_rebuilt_deterministically(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        first = build_project_state(ledger, _repo_root()).to_dict()
        second = build_project_state(ledger, _repo_root()).to_dict()
        assert first == second
    finally:
        ledger.close()


def test_prompt_files_are_inventoried(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = build_project_state(ledger, _repo_root())
        prompts = state.prompt_backlog.value
        assert state.prompt_backlog.source == "prompt_files"
        assert len(prompts) == 28
        assert prompts[0]["number"] == 1
        assert prompts[-1]["number"] == 28
    finally:
        ledger.close()


def test_prompt_20_is_next_unimplemented_numeric_prompt(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = build_project_state(ledger, _repo_root())
        assert state.next_unimplemented_prompt_number.source == "prompt_files"
        assert state.next_unimplemented_prompt_number.value == 20
    finally:
        ledger.close()


def test_prompts_20_through_28_are_postponed_by_deterministic_rule(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = build_project_state(ledger, _repo_root())
        assert state.postponed_prompts.value == list(range(20, 29))
        statuses = {item["number"]: item["status"] for item in state.prompt_backlog.value}
        assert {statuses[number] for number in range(1, 20)} == {"implemented"}
        assert {statuses[number] for number in range(20, 29)} == {"postponed"}
    finally:
        ledger.close()


def test_filesystem_derived_observations_are_source_labeled(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = build_project_state(ledger, _repo_root())
        assert state.known_features.source == "static_file"
        assert state.readme_source_drift.source == "static_file"
        assert state.current_failing_tests.source == "static_file"
        assert state.missing_capabilities.source == "static_file"
    finally:
        ledger.close()


def test_readme_drift_can_be_surfaced_when_detectable(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = build_project_state(ledger, _repo_root())
        drift = state.readme_source_drift.value
        assert drift["exists"]
        assert drift["readme_highest_version"] == "v1.3"
        assert drift["source_highest_version"] == "v1.8"
        assert drift["detected"]
    finally:
        ledger.close()


def test_no_ledger_mutation_occurs(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        before_rows = ledger.connection.execute("SELECT * FROM ledger_events").fetchall()
        build_project_state(ledger, _repo_root())
        after_rows = ledger.connection.execute("SELECT * FROM ledger_events").fetchall()
        assert after_rows == before_rows
    finally:
        ledger.close()


def test_no_mind_adapter_proposal_approval_or_cli_code_is_added():
    root = _repo_root()
    assert not (root / "leira/minds").exists()
    assert not (root / "leira/proposals").exists()
    assert not (root / "leira/cli").exists()
