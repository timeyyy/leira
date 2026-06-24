"""Leira v1.9 project state view: explicit evidence, not authority."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from leira.audit.auditor import audit
from leira.dispatcher.kernel import LedgerKernel

Source = str

_PROMPT_TITLE_RE = re.compile(r"^Implement Leira (?P<version>[^:]+): (?P<title>.+)$")
_README_VERSION_RE = re.compile(r"\bv(?P<major>\d+)\.(?P<minor>\d+)\b|\bv(?P<zero>0)\b")

_KNOWN_FEATURES: tuple[tuple[str, str], ...] = (
    ("ledger", "leira/dispatcher/kernel.py"),
    ("lifecycle", "leira/dispatcher/lifecycle.py"),
    ("dispatcher", "leira/dispatcher/dispatcher.py"),
    ("workers", "leira/workers/base.py"),
    ("inbox", "leira/inbox/inbox.py"),
    ("registry", "leira/registry/registry.py"),
    ("claims", "leira/claims/claims.py"),
    ("receipts", "leira/receipts/receipts.py"),
    ("workspace", "leira/workspace/workspace.py"),
    ("provenance", "leira/provenance/git_provenance.py"),
    ("sessions", "leira/sessions/sessions.py"),
    ("archive_replay", "leira/archive/replay.py"),
    ("environment_snapshots", "leira/environment/environment.py"),
)

_MISSING_CAPABILITIES: tuple[str, ...] = (
    "minds",
    "proposal_system",
    "approval_system",
    "cli",
    "project_state_feedback_loop",
)

_POSTPONED_PROMPTS = frozenset(range(20, 29))


@dataclass(frozen=True)
class Evidence:
    """One source-labeled value in the project state view."""

    source: Source
    value: Any


@dataclass(frozen=True)
class PromptInventoryItem:
    number: int
    path: str
    title: str
    version: str | None
    status: str
    source: Source = "prompt_files"


@dataclass(frozen=True)
class ProjectState:
    """A deterministic, read-only view over current project evidence."""

    ledger_health: Evidence
    audit_health: Evidence
    known_features: Evidence
    prompt_backlog: Evidence
    next_unimplemented_prompt_number: Evidence
    postponed_prompts: Evidence
    readme_source_drift: Evidence
    current_failing_tests: Evidence
    missing_capabilities: Evidence
    unreconciled_disagreement: Evidence

    def to_dict(self) -> dict:
        return asdict(self)


def build_project_state(
    ledger: LedgerKernel,
    repo_root: str | Path = ".",
    *,
    postponed_prompts: set[int] | frozenset[int] | None = None,
    workspace_root: str | Path | None = None,
) -> ProjectState:
    """Build a read-only project state view from ledger and repository evidence.

    This function records nothing. It does not rebuild projections, submit
    intents, dispatch workers, or reconcile disagreements. Audit findings are
    surfaced as open evidence.
    """

    root = Path(repo_root)
    postponed = _POSTPONED_PROMPTS if postponed_prompts is None else frozenset(postponed_prompts)
    audit_result = audit(ledger, workspace_root)
    prompt_items = _prompt_inventory(root, postponed)

    return ProjectState(
        ledger_health=Evidence("ledger", _ledger_health(ledger)),
        audit_health=Evidence(
            "audit",
            {
                "success": audit_result.success,
                "chain_valid": audit_result.chain_valid,
                "projections_valid": audit_result.projections_valid,
                "error_count": len(audit_result.errors),
                "errors": list(audit_result.errors),
            },
        ),
        known_features=Evidence("static_file", _known_features(root)),
        prompt_backlog=Evidence("prompt_files", [asdict(item) for item in prompt_items]),
        next_unimplemented_prompt_number=Evidence(
            "prompt_files", _next_unimplemented_prompt_number(prompt_items)
        ),
        postponed_prompts=Evidence(
            "prompt_files",
            [item.number for item in prompt_items if item.status == "postponed"],
        ),
        readme_source_drift=Evidence("static_file", _readme_source_drift(root)),
        current_failing_tests=Evidence(
            "static_file",
            {
                "evidence_available": False,
                "tests": [],
                "note": "No durable test-result evidence is recorded in the current project state inputs.",
            },
        ),
        missing_capabilities=Evidence("static_file", _missing_capabilities(root)),
        unreconciled_disagreement=Evidence(
            "audit",
            {
                "exists": bool(audit_result.errors),
                "errors": list(audit_result.errors),
            },
        ),
    )


def _ledger_health(ledger: LedgerKernel) -> dict:
    total = ledger.connection.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0]
    rows = ledger.connection.execute(
        """
        SELECT event_type, COUNT(*)
        FROM ledger_events
        GROUP BY event_type
        ORDER BY event_type
        """
    ).fetchall()
    chain = ledger.validate_chain()
    return {
        "event_count": total,
        "event_type_counts": {event_type: count for event_type, count in rows},
        "chain_valid": chain.success,
        "chain_events_checked": chain.events_checked,
    }


def _known_features(root: Path) -> list[dict]:
    features: list[dict] = []
    for name, relative_path in _KNOWN_FEATURES:
        path = root / relative_path
        features.append(
            {
                "name": name,
                "present": path.is_file(),
                "path": relative_path,
            }
        )
    return features


def _prompt_inventory(root: Path, postponed: frozenset[int]) -> list[PromptInventoryItem]:
    prompts_root = root / "prompts"
    files = sorted(prompts_root.rglob("*.txt"), key=_prompt_sort_key) if prompts_root.exists() else []
    items: list[PromptInventoryItem] = []
    for path in files:
        number = _prompt_number(path)
        if number is None:
            continue
        title_line = _first_nonempty_line(path)
        version, title = _parse_prompt_title(title_line)
        items.append(
            PromptInventoryItem(
                number=number,
                path=path.relative_to(root).as_posix(),
                title=title,
                version=version,
                status=_prompt_status(number, postponed),
            )
        )
    return items


def _prompt_sort_key(path: Path) -> tuple[int, str]:
    number = _prompt_number(path)
    return (number if number is not None else 10**9, path.as_posix())


def _prompt_number(path: Path) -> int | None:
    try:
        return int(path.stem)
    except ValueError:
        return None


def _first_nonempty_line(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    except OSError:
        return ""
    return ""


def _parse_prompt_title(line: str) -> tuple[str | None, str]:
    match = _PROMPT_TITLE_RE.match(line)
    if match:
        return match.group("version"), match.group("title")
    return None, line


def _prompt_status(number: int, postponed: frozenset[int]) -> str:
    if number in postponed:
        return "postponed"
    if 1 <= number <= 19:
        return "implemented"
    return "not_implemented"


def _next_unimplemented_prompt_number(items: list[PromptInventoryItem]) -> int | None:
    for item in sorted(items, key=lambda prompt: prompt.number):
        if item.status != "implemented":
            return item.number
    return None


def _readme_source_drift(root: Path) -> dict:
    readme = root / "README.md"
    if not readme.exists():
        return {
            "exists": False,
            "detected": False,
            "readme_highest_version": None,
            "source_highest_version": _source_highest_version(root),
        }
    text = readme.read_text(encoding="utf-8")
    readme_highest = _highest_version(text)
    source_highest = _source_highest_version(root)
    return {
        "exists": True,
        "detected": readme_highest != source_highest,
        "readme_highest_version": readme_highest,
        "source_highest_version": source_highest,
    }


def _highest_version(text: str) -> str | None:
    versions: list[tuple[int, int]] = []
    for match in _README_VERSION_RE.finditer(text):
        if match.group("zero") is not None:
            versions.append((0, 0))
        else:
            versions.append((int(match.group("major")), int(match.group("minor"))))
    if not versions:
        return None
    major, minor = max(versions)
    return "v0" if (major, minor) == (0, 0) else f"v{major}.{minor}"


def _source_highest_version(root: Path) -> str | None:
    if (root / "leira/environment/environment.py").is_file():
        return "v1.8"
    if (root / "leira/archive/replay.py").is_file():
        return "v1.7"
    if (root / "leira/sessions/sessions.py").is_file():
        return "v1.6"
    if (root / "leira/provenance/git_provenance.py").is_file():
        return "v1.5"
    if (root / "leira/workspace/workspace.py").is_file():
        return "v1.4"
    if (root / "leira/receipts/receipts.py").is_file():
        return "v1.3"
    return None


def _missing_capabilities(root: Path) -> list[dict]:
    checks = {
        "minds": root / "leira/minds",
        "proposal_system": root / "leira/proposals",
        "approval_system": root / "leira/proposals/approval.py",
        "cli": root / "leira/cli",
        "project_state_feedback_loop": root / "leira/project_state/feedback.py",
    }
    missing = []
    for name in _MISSING_CAPABILITIES:
        path = checks[name]
        if not path.exists():
            missing.append({"name": name, "missing": True})
    return missing
