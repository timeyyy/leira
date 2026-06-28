"""Leira v1.19 verification prompt drafting: a reviewable request, not a verification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROVENANCE_NOTICE = (
    "This prompt is a deterministic draft for requesting verification.\n"
    "It performs no verification, execution, approval, rejection or dispatch."
)

DEFAULT_OUTPUT_SECTIONS: tuple[str, ...] = (
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


@dataclass(frozen=True)
class VerificationPrompt:
    prompt_id: str
    slice_label: str
    subject_label: str
    subject_path: str
    expected_test_command: str
    required_checks: tuple[str, ...]
    guardrails: tuple[str, ...]
    output_sections: tuple[str, ...]
    source_label: str


def create_verification_prompt(
    *,
    prompt_id: str,
    slice_label: str,
    subject_label: str,
    subject_path: str,
    expected_test_command: str,
    required_checks: list[str] | tuple[str, ...],
    guardrails: list[str] | tuple[str, ...],
    output_sections: list[str] | tuple[str, ...],
    source_label: str,
) -> VerificationPrompt:
    """Create one immutable verification prompt from caller-supplied evidence."""

    return VerificationPrompt(
        prompt_id=prompt_id,
        slice_label=slice_label,
        subject_label=subject_label,
        subject_path=subject_path,
        expected_test_command=expected_test_command,
        required_checks=tuple(required_checks),
        guardrails=tuple(guardrails),
        output_sections=tuple(output_sections),
        source_label=source_label,
    )


def verification_prompt_markdown(prompt: VerificationPrompt) -> str:
    """Render one verification prompt as deterministic markdown."""

    lines = [
        "# Verification Prompt",
        "",
        "## Prompt ID",
        "",
        prompt.prompt_id,
        "",
        "## Slice",
        "",
        prompt.slice_label,
        "",
        "## Subject",
        "",
        prompt.subject_label,
        "",
        "## Subject Path",
        "",
        prompt.subject_path,
        "",
        "## Expected Test Command",
        "",
        "```text",
        prompt.expected_test_command,
        "```",
        "",
        "## Required Checks",
        "",
    ]
    lines.extend(f"* {entry}" for entry in prompt.required_checks)
    lines.extend(["", "## Guardrails", ""])
    lines.extend(f"* {entry}" for entry in prompt.guardrails)
    lines.extend(["", "## Output Sections", ""])
    lines.extend(f"{index}. {entry}" for index, entry in enumerate(prompt.output_sections, start=1))
    lines.extend(
        [
            "",
            "## Source",
            "",
            prompt.source_label,
            "",
            "## Provenance Notice",
            "",
            PROVENANCE_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_verification_prompt(prompt: VerificationPrompt, repo_root: str | Path = ".") -> str:
    """Write deterministic derived verification-prompt markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "verification_prompts" / f"{prompt.prompt_id}.verification_prompt.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(verification_prompt_markdown(prompt), encoding="utf-8")
    return output.relative_to(root).as_posix()
