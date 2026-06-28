from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from leira.dispatcher.kernel import LedgerKernel
from leira.inbox.inbox import InboxKernel
from leira.project_state.state import build_project_state
from leira.verification_prompt.prompt import (
    DEFAULT_OUTPUT_SECTIONS,
    PROVENANCE_NOTICE,
    VerificationPrompt,
    create_verification_prompt,
    verification_prompt_markdown,
    write_verification_prompt,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_rows(ledger):
    return ledger.connection.execute("SELECT * FROM ledger_events ORDER BY rowid").fetchall()


def _prompt():
    return create_verification_prompt(
        prompt_id="prompt-1",
        slice_label="Leira v1.18 -- Verification Record",
        subject_label="VerificationRecord",
        subject_path="leira/verification_record/",
        expected_test_command="python3 -m pytest leira/verification_record/test_verification_record.py -v",
        required_checks=("immutable dataclass", "deterministic markdown"),
        guardrails=("no execution", "no ledger mutation"),
        output_sections=("Executive summary", "Files changed"),
        source_label="human_operator",
    )


def test_immutable_dataclass():
    prompt = _prompt()
    with pytest.raises(FrozenInstanceError):
        prompt.subject_label = "Other"


def test_deterministic_object_creation():
    assert _prompt() == _prompt()
    assert isinstance(_prompt(), VerificationPrompt)


def test_deterministic_markdown():
    first = verification_prompt_markdown(_prompt())
    second = verification_prompt_markdown(_prompt())
    assert first == second
    assert first.startswith("# Verification Prompt\n")


def test_byte_identical_repeated_rendering():
    first = verification_prompt_markdown(_prompt()).encode("utf-8")
    second = verification_prompt_markdown(_prompt()).encode("utf-8")
    assert first == second


def test_deterministic_file_output(tmp_path):
    prompt = _prompt()
    path = write_verification_prompt(prompt, tmp_path)
    assert path == ".leira/verification_prompts/prompt-1.verification_prompt.md"
    assert (tmp_path / path).read_text(encoding="utf-8") == verification_prompt_markdown(prompt)


def test_byte_identical_repeated_writes(tmp_path):
    prompt = _prompt()
    first_path = write_verification_prompt(prompt, tmp_path)
    first_bytes = (tmp_path / first_path).read_bytes()
    second_path = write_verification_prompt(prompt, tmp_path)
    second_bytes = (tmp_path / second_path).read_bytes()
    assert first_path == second_path
    assert first_bytes == second_bytes


def test_slice_label_preserved_exactly():
    label = "Leira v1.19 -- Verification Prompt Drafter"
    prompt = create_verification_prompt(
        prompt_id="prompt-1",
        slice_label=label,
        subject_label="subject",
        subject_path="leira/subject/",
        expected_test_command="pytest",
        required_checks=(),
        guardrails=(),
        output_sections=(),
        source_label="source",
    )
    assert prompt.slice_label == label
    assert label in verification_prompt_markdown(prompt)


def test_subject_label_preserved_exactly():
    label = " VerificationPrompt / draft "
    prompt = create_verification_prompt(
        prompt_id="prompt-1",
        slice_label="slice",
        subject_label=label,
        subject_path="leira/subject/",
        expected_test_command="pytest",
        required_checks=(),
        guardrails=(),
        output_sections=(),
        source_label="source",
    )
    assert prompt.subject_label == label
    assert label in verification_prompt_markdown(prompt)


def test_subject_path_preserved_exactly():
    path_value = "leira/verification_prompt/"
    prompt = create_verification_prompt(
        prompt_id="prompt-1",
        slice_label="slice",
        subject_label="subject",
        subject_path=path_value,
        expected_test_command="pytest",
        required_checks=(),
        guardrails=(),
        output_sections=(),
        source_label="source",
    )
    assert prompt.subject_path == path_value
    assert path_value in verification_prompt_markdown(prompt)


def test_expected_test_command_preserved_exactly():
    command = "python3 -m pytest leira/verification_prompt/test_verification_prompt.py -v"
    prompt = create_verification_prompt(
        prompt_id="prompt-1",
        slice_label="slice",
        subject_label="subject",
        subject_path="leira/subject/",
        expected_test_command=command,
        required_checks=(),
        guardrails=(),
        output_sections=(),
        source_label="source",
    )
    assert prompt.expected_test_command == command
    assert f"```text\n{command}\n```" in verification_prompt_markdown(prompt)


def test_required_checks_ordering_preserved_exactly():
    prompt = _prompt()
    assert prompt.required_checks == ("immutable dataclass", "deterministic markdown")
    markdown = verification_prompt_markdown(prompt)
    assert markdown.index("* immutable dataclass") < markdown.index("* deterministic markdown")


def test_guardrails_ordering_preserved_exactly():
    prompt = _prompt()
    assert prompt.guardrails == ("no execution", "no ledger mutation")
    markdown = verification_prompt_markdown(prompt)
    assert markdown.index("* no execution") < markdown.index("* no ledger mutation")


def test_output_sections_ordering_preserved_exactly():
    prompt = _prompt()
    assert prompt.output_sections == ("Executive summary", "Files changed")
    markdown = verification_prompt_markdown(prompt)
    assert markdown.index("1. Executive summary") < markdown.index("2. Files changed")


def test_default_output_sections_constant_order():
    assert DEFAULT_OUTPUT_SECTIONS == (
        "Executive summary",
        "Files changed",
        "Public API",
        "Test command and results",
        "Requirements matrix",
        "Guardrail audit",
        "Determinism audit",
        "Mutation audit",
        "Remaining technical debt",
        "Suggested next slice",
    )
    prompt = create_verification_prompt(
        prompt_id="prompt-1",
        slice_label="slice",
        subject_label="subject",
        subject_path="leira/subject/",
        expected_test_command="pytest",
        required_checks=(),
        guardrails=(),
        output_sections=DEFAULT_OUTPUT_SECTIONS,
        source_label="source",
    )
    markdown = verification_prompt_markdown(prompt)
    assert markdown.index("1. Executive summary") < markdown.index("10. Suggested next slice")


def test_source_label_preserved_exactly():
    label = "external/ci-runner"
    prompt = create_verification_prompt(
        prompt_id="prompt-1",
        slice_label="slice",
        subject_label="subject",
        subject_path="leira/subject/",
        expected_test_command="pytest",
        required_checks=(),
        guardrails=(),
        output_sections=(),
        source_label=label,
    )
    assert prompt.source_label == label
    assert label in verification_prompt_markdown(prompt)


def test_section_order_never_varies():
    markdown = verification_prompt_markdown(_prompt())
    sections = [
        "# Verification Prompt",
        "## Prompt ID",
        "## Slice",
        "## Subject",
        "## Subject Path",
        "## Expected Test Command",
        "## Required Checks",
        "## Guardrails",
        "## Output Sections",
        "## Source",
        "## Provenance Notice",
    ]
    positions = [markdown.index(section) for section in sections]
    assert positions == sorted(positions)


def test_no_clocks_timestamps_uuid_or_randomness():
    source = (_repo_root() / "leira/verification_prompt/prompt.py").read_text(encoding="utf-8")
    forbidden = ("datetime", "time", "timestamp", "uuid", "random", "generate")
    assert all(term not in source for term in forbidden)


def test_no_generated_identifiers():
    first = create_verification_prompt(
        prompt_id="prompt-1",
        slice_label="slice",
        subject_label="subject",
        subject_path="leira/subject/",
        expected_test_command="pytest",
        required_checks=(),
        guardrails=(),
        output_sections=(),
        source_label="source",
    )
    second = create_verification_prompt(
        prompt_id="prompt-1",
        slice_label="slice",
        subject_label="subject",
        subject_path="leira/subject/",
        expected_test_command="pytest",
        required_checks=(),
        guardrails=(),
        output_sections=(),
        source_label="source",
    )
    assert first.prompt_id == second.prompt_id == "prompt-1"


def test_no_test_execution_file_inspection_or_verification_logic():
    prompt = _prompt()
    assert not hasattr(prompt, "run")
    assert not hasattr(prompt, "verify")
    assert not hasattr(prompt, "inspect")
    assert not hasattr(prompt, "results")
    assert not hasattr(prompt, "passed")


def test_no_subprocess_shell_git_or_pytest_calls():
    source = (_repo_root() / "leira/verification_prompt/prompt.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "Popen", "exec(", "eval(", "git ", "pytest.main", "shutil")
    assert all(term not in source for term in forbidden)


def test_no_approval_rejection_or_dispatch():
    prompt = _prompt()
    assert not hasattr(prompt, "approved")
    assert not hasattr(prompt, "rejected")
    assert not hasattr(prompt, "dispatch")
    source = (_repo_root() / "leira/verification_prompt/prompt.py").read_text(encoding="utf-8")
    assert "dispatcher" not in source
    assert "def approve" not in source
    assert "def reject" not in source
    assert "def dispatch" not in source


def test_no_ledger_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = _event_rows(ledger)
        verification_prompt_markdown(_prompt())
        assert _event_rows(ledger) == before
    finally:
        ledger.close()


def test_no_project_state_mutation(tmp_path):
    ledger = LedgerKernel(str(tmp_path / "ledger.sqlite3"))
    try:
        InboxKernel(ledger).submit_intent("worker", {"message": "hi"})
        before = build_project_state(ledger, repo_root=str(_repo_root()))
        verification_prompt_markdown(_prompt())
        write_verification_prompt(_prompt(), tmp_path)
        after = build_project_state(ledger, repo_root=str(_repo_root()))
        assert before == after
    finally:
        ledger.close()


def test_no_planner_or_ai_calls():
    source = (_repo_root() / "leira/verification_prompt/prompt.py").read_text(encoding="utf-8")
    forbidden = ("planner", "openai", "anthropic", "claude_api", "browser")
    assert all(term not in source.lower() for term in forbidden)


def test_no_forbidden_code_added():
    root = _repo_root()
    forbidden = [
        root / "leira/verification_prompt/dispatcher.py",
        root / "leira/verification_prompt/dispatch.py",
        root / "leira/verification_prompt/approval.py",
        root / "leira/verification_prompt/rejection.py",
        root / "leira/verification_prompt/planner.py",
        root / "leira/verification_prompt/cli.py",
        root / "leira/verification_prompt/openai.py",
        root / "leira/verification_prompt/claude.py",
        root / "leira/verification_prompt/browser.py",
        root / "leira/verification_prompt/database.py",
        root / "leira/verification_prompt/execution.py",
        root / "leira/verification_prompt/subprocess.py",
        root / "leira/verification_prompt/git.py",
        root / "leira/verification_prompt/verifier.py",
    ]
    assert all(not path.exists() for path in forbidden)


def test_provenance_notice_present():
    markdown = verification_prompt_markdown(_prompt())
    assert PROVENANCE_NOTICE in markdown
