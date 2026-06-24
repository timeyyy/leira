"""Leira v1.10 deterministic prompt drafter: packet, not planner."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from leira.project_state.state import ProjectState

_INVARIANTS_PATH = ".leira/invariants.md"

_DEFAULT_INVARIANTS = (
    "* Ledger remains truth.\n"
    "* Projections remain disposable.\n"
    "* No minds.\n"
    "* No agents.\n"
    "* No dispatch.\n"
    "* No approval.\n"
    "* No CLI.\n"
)

_DO_NOT_IMPLEMENT = (
    "* Do not implement minds.\n"
    "* Do not implement agents.\n"
    "* Do not implement LLM adapters.\n"
    "* Do not implement proposal generation.\n"
    "* Do not implement approval workflow.\n"
    "* Do not implement CLI.\n"
    "* Do not dispatch work.\n"
    "* Do not mutate ledger state.\n"
    "* Do not add timestamps.\n"
)

_INELIGIBLE_STATUSES = frozenset(
    {"implemented", "postponed", "superseded", "conflicts", "conflicting"}
)


@dataclass(frozen=True)
class DraftResult:
    status: str
    reason_code: str | None
    source_labels: tuple[str, ...]
    prompt_number: int | None = None
    markdown: str | None = None
    output_path: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def draft_prompt(
    project_state: ProjectState,
    repo_root: str | Path = ".",
    *,
    write: bool = False,
    open_proposal_disagreements: list[str] | None = None,
) -> DraftResult:
    """Return a deterministic markdown draft packet or deterministic refusal data.

    The project state is treated as input evidence. This function does not
    inspect clocks, mutate the ledger, dispatch work, approve anything, or
    resolve disagreements.
    """

    root = Path(repo_root)
    source_labels = _source_labels(project_state)

    if _ledger_failed(project_state):
        return _refusal("ledger_health_failed", source_labels)

    if _audit_inconsistency(project_state):
        return _refusal("audit_inconsistency", source_labels)

    selected = _select_prompt(project_state)
    if selected is None:
        return _refusal("no_eligible_prompt", source_labels)

    prompt_path = root / selected["path"]
    raw_prompt = prompt_path.read_text(encoding="utf-8")
    invariants, invariants_source = _load_invariants(root)
    authorized_scope, authorized_scope_source = _authorized_scope(root, selected["number"])
    markdown = _render_markdown(
        project_state=project_state,
        selected=selected,
        raw_prompt=raw_prompt,
        invariants=invariants,
        invariants_source=invariants_source,
        authorized_scope=authorized_scope,
        authorized_scope_source=authorized_scope_source,
        open_proposal_disagreements=open_proposal_disagreements or [],
    )

    output_path = None
    if write:
        output = root / ".leira" / "outbox" / f"draft_{selected['number']}.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        output_path = output.relative_to(root).as_posix()

    return DraftResult(
        status="drafted",
        reason_code=None,
        source_labels=tuple(sorted(set(source_labels + ("default_static", invariants_source, authorized_scope_source)))),
        prompt_number=selected["number"],
        markdown=markdown,
        output_path=output_path,
    )


def _refusal(reason_code: str, source_labels: tuple[str, ...]) -> DraftResult:
    return DraftResult(
        status="no_eligible_prompt" if reason_code == "no_eligible_prompt" else "refused",
        reason_code=reason_code,
        source_labels=tuple(sorted(set(source_labels))),
    )


def _source_labels(project_state: ProjectState) -> tuple[str, ...]:
    labels = []
    for value in project_state.to_dict().values():
        source = value.get("source") if isinstance(value, dict) else None
        if isinstance(source, str):
            labels.append(source)
    return tuple(sorted(set(labels)))


def _ledger_failed(project_state: ProjectState) -> bool:
    value = project_state.ledger_health.value
    if not isinstance(value, dict):
        return True
    if value.get("status") == "failed":
        return True
    return value.get("chain_valid") is False


def _audit_inconsistency(project_state: ProjectState) -> bool:
    value = project_state.audit_health.value
    if isinstance(value, dict):
        if value.get("success") is False:
            return True
        if value.get("error_count", 0):
            return True
    disagreement = project_state.unreconciled_disagreement.value
    if isinstance(disagreement, dict) and disagreement.get("exists") is True:
        return True
    return False


def _select_prompt(project_state: ProjectState) -> dict[str, Any] | None:
    prompts = project_state.prompt_backlog.value
    if not isinstance(prompts, list):
        return None
    candidates = [
        prompt
        for prompt in prompts
        if isinstance(prompt, dict)
        and isinstance(prompt.get("number"), int)
        and prompt.get("status") not in _INELIGIBLE_STATUSES
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda prompt: prompt["number"])[0]


def _load_invariants(root: Path) -> tuple[str, str]:
    path = root / _INVARIANTS_PATH
    if path.is_file():
        return path.read_text(encoding="utf-8"), "static_file"
    return _DEFAULT_INVARIANTS, "default_static"


def _authorized_scope(root: Path, prompt_number: int) -> tuple[str, str]:
    path = root / ".leira" / "authorized_scope.json"
    if not path.is_file():
        return "authorized_scope=unknown", "default_static"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "authorized_scope=unknown", "static_file"
    value = data.get(str(prompt_number)) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        return "authorized_scope=unknown", "static_file"
    return value, "static_file"


def _render_markdown(
    *,
    project_state: ProjectState,
    selected: dict[str, Any],
    raw_prompt: str,
    invariants: str,
    invariants_source: str,
    authorized_scope: str,
    authorized_scope_source: str,
    open_proposal_disagreements: list[str],
) -> str:
    lines = [
        "# Deterministic Draft Packet",
        "",
        "## Selected Prompt Identifier",
        "",
        f"prompt_number: {selected['number']}",
        f"prompt_path: {selected['path']}",
        f"prompt_status: {selected['status']}",
        f"source: {selected.get('source', 'prompt_files')}",
        "",
        "## Raw Prompt Text",
        "",
        "```text",
        raw_prompt.rstrip("\n"),
        "```",
        "",
        "## Project State Snapshot",
        "",
        "```json",
        _stable_json(project_state.to_dict()),
        "```",
        "",
        "## Source-Labeled Evidence",
        "",
        _source_label_lines(project_state),
        "",
        "## Frozen Invariants",
        "",
        f"source: {invariants_source}",
        "",
        invariants.rstrip("\n"),
        "",
        "## Authorized Scope",
        "",
        f"source: {authorized_scope_source}",
        "",
        authorized_scope,
    ]

    note_lines = _relevant_prompt_notes(project_state, selected["number"])
    if note_lines:
        lines.extend(["", "## Postponed/Superseded/Conflicting Prompt Notes", ""])
        lines.extend(note_lines)

    if open_proposal_disagreements:
        lines.extend(["", "## Open Proposal Disagreements", ""])
        lines.extend(f"* {item}" for item in open_proposal_disagreements)

    lines.extend(["", "## Explicit Do-Not-Implement", "", _DO_NOT_IMPLEMENT.rstrip("\n"), ""])
    return "\n".join(lines)


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2)


def _source_label_lines(project_state: ProjectState) -> str:
    rows = []
    for name, value in project_state.to_dict().items():
        if isinstance(value, dict):
            rows.append(f"* {name}: {value.get('source')}")
    return "\n".join(rows)


def _relevant_prompt_notes(project_state: ProjectState, selected_number: int) -> list[str]:
    notes = []
    prompts = project_state.prompt_backlog.value
    if not isinstance(prompts, list):
        return notes
    for prompt in prompts:
        if not isinstance(prompt, dict):
            continue
        status = prompt.get("status")
        number = prompt.get("number")
        if status in {"postponed", "superseded", "conflicts", "conflicting"} and isinstance(number, int):
            if number < selected_number:
                notes.append(f"* prompt {number}: {status}")
    return notes
