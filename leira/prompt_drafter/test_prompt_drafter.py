from dataclasses import replace
from pathlib import Path

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.project_state.state import Evidence, build_project_state
from leira.prompt_drafter.drafter import draft_prompt


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _head_hash(ledger):
    row = ledger.connection.execute(
        "SELECT event_hash FROM ledger_events ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _state_with_prompts(project_state, prompts):
    return replace(
        project_state,
        prompt_backlog=Evidence("prompt_files", prompts),
        next_unimplemented_prompt_number=Evidence("prompt_files", None),
        postponed_prompts=Evidence(
            "prompt_files",
            [prompt["number"] for prompt in prompts if prompt.get("status") == "postponed"],
        ),
    )


def _clean_state(ledger, repo_root=None):
    return build_project_state(ledger, repo_root or _repo_root(), postponed_prompts=frozenset())


def test_deterministic_draft_output(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = _clean_state(ledger)
        first = draft_prompt(state, _repo_root())
        second = draft_prompt(state, _repo_root())
        assert first.status == "drafted"
        assert first.markdown == second.markdown
        assert first.to_json() == second.to_json()
    finally:
        ledger.close()


def test_deterministic_refusal_output(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = build_project_state(ledger, _repo_root())
        first = draft_prompt(state, _repo_root())
        second = draft_prompt(state, _repo_root())
        assert first.status == "no_eligible_prompt"
        assert first.reason_code == "no_eligible_prompt"
        assert first.to_json() == second.to_json()
    finally:
        ledger.close()


def test_no_ledger_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        state = _clean_state(ledger)
        before = _event_rows(ledger)
        draft_prompt(state, _repo_root())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()


def test_repeated_run_with_same_input_writes_byte_identical_draft(tmp_path):
    repo = tmp_path / "repo"
    prompts = repo / "prompts" / "executed"
    prompts.mkdir(parents=True)
    (prompts / "1.txt").write_text("Implement Leira v0: done\n", encoding="utf-8")
    (prompts / "20.txt").write_text("Implement Leira v2.0: Draft Me\n\nBody\n", encoding="utf-8")
    (repo / "README.md").write_text("# Leira v0\n", encoding="utf-8")
    (repo / "leira" / "dispatcher").mkdir(parents=True)
    (repo / "leira" / "dispatcher" / "kernel.py").write_text("", encoding="utf-8")

    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = build_project_state(ledger, repo, postponed_prompts=frozenset())
        first = draft_prompt(state, repo, write=True)
        first_bytes = (repo / first.output_path).read_bytes()
        second = draft_prompt(state, repo, write=True)
        second_bytes = (repo / second.output_path).read_bytes()
        assert first_bytes == second_bytes
        assert first.markdown.encode("utf-8") == first_bytes
    finally:
        ledger.close()


def test_repeated_run_leaves_ledger_hash_unchanged(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        state = _clean_state(ledger)
        before = _head_hash(ledger)
        draft_prompt(state, _repo_root())
        draft_prompt(state, _repo_root())
        assert _head_hash(ledger) == before
    finally:
        ledger.close()


def test_audit_inconsistency_blocks(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = _clean_state(ledger)
        bad = replace(
            state,
            audit_health=Evidence(
                "audit",
                {
                    "success": False,
                    "chain_valid": True,
                    "projections_valid": True,
                    "error_count": 1,
                    "errors": ["BROKEN"],
                },
            ),
            unreconciled_disagreement=Evidence("audit", {"exists": True, "errors": ["BROKEN"]}),
        )
        result = draft_prompt(bad, _repo_root())
        assert result.status == "refused"
        assert result.reason_code == "audit_inconsistency"
        assert result.markdown is None
    finally:
        ledger.close()


def test_open_proposal_disagreements_render(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = _clean_state(ledger)
        result = draft_prompt(
            state,
            _repo_root(),
            open_proposal_disagreements=["seat A wants preservation", "seat B wants composting"],
        )
        assert "## Open Proposal Disagreements" in result.markdown
        assert "seat A wants preservation" in result.markdown
        assert "seat B wants composting" in result.markdown
    finally:
        ledger.close()


def test_prompt_20_postponed_means_prompt_20_is_not_selected(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = build_project_state(ledger, _repo_root())
        result = draft_prompt(state, _repo_root())
        assert result.status == "no_eligible_prompt"
        assert result.prompt_number is None
    finally:
        ledger.close()


def test_selection_rule_is_total_and_mechanical(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = _clean_state(ledger)
        prompts = [
            {"number": 20, "path": "prompts/executed/20.txt", "title": "x", "version": "v2.0", "status": "postponed", "source": "prompt_files"},
            {"number": 21, "path": "prompts/executed/21.txt", "title": "x", "version": "v2.1", "status": "superseded", "source": "prompt_files"},
            {"number": 22, "path": "prompts/executed/22.txt", "title": "x", "version": "v2.2", "status": "conflicts", "source": "prompt_files"},
            {"number": 23, "path": "prompts/executed/23.txt", "title": "x", "version": "v2.3", "status": "not_implemented", "source": "prompt_files"},
            {"number": 24, "path": "prompts/executed/24.txt", "title": "x", "version": "v2.4", "status": "not_implemented", "source": "prompt_files"},
        ]
        state = _state_with_prompts(state, prompts)
        result = draft_prompt(state, _repo_root())
        assert result.status == "drafted"
        assert result.prompt_number == 23
    finally:
        ledger.close()


def test_source_labels_are_preserved(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        state = _clean_state(ledger)
        result = draft_prompt(state, _repo_root())
        assert {"ledger", "audit", "prompt_files", "static_file"}.issubset(
            set(result.source_labels)
        )
        assert "## Source-Labeled Evidence" in result.markdown
        assert "* ledger_health: ledger" in result.markdown
    finally:
        ledger.close()


def test_no_minds_adapters_proposal_approval_or_cli_added():
    root = _repo_root()
    assert not (root / "leira/minds").exists()
    assert not (root / "leira/proposals").exists()
    assert not (root / "leira/cli").exists()
    assert not (root / "leira/prompt_drafter/openai.py").exists()
    assert not (root / "leira/prompt_drafter/claude.py").exists()
