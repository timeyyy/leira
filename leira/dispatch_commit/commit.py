"""Leira v3.4 dispatch commit: explicit human conversion of a draft into a record.

This module converts an already-built DispatchDraft into a real
DispatchRecord, but only when a human explicitly calls `commit_dispatch`.
It calls the existing `create_dispatch_record` exactly once with fields
extracted directly from the draft and the caller-supplied commit details
-- nothing is inferred, and no field is altered along the way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from leira.dispatch_draft.draft import DispatchDraft
from leira.dispatch_record.dispatch import (
    DispatchRecord,
    create_dispatch_record,
    dispatch_record_markdown,
)

PROVENANCE_NOTICE = (
    "This commit records that a human explicitly chose to convert a Dispatch Draft "
    "into a Dispatch Record.\n"
    "No planning, execution, approval or dispatch occurs here."
)


@dataclass(frozen=True)
class DispatchCommit:
    commit_id: str
    draft_id: str
    dispatch_record: DispatchRecord


def commit_dispatch(
    draft: DispatchDraft,
    *,
    dispatch_id: str,
    human_decision_id: str,
    source_label: str,
    dispatch_summary: str,
    target_label: str,
    execution_mode: str,
) -> DispatchCommit:
    """Convert an explicitly approved DispatchDraft into a DispatchRecord.

    This function exists only to be called when a human has explicitly
    decided to commit a draft. It calls `create_dispatch_record` exactly
    once, passing through the draft's own fields and the caller-supplied
    commit details unmodified.
    """

    dispatch_record = create_dispatch_record(
        dispatch_id=dispatch_id,
        human_decision_id=human_decision_id,
        subject_id=draft.subject_id,
        subject_kind=draft.subject_kind,
        dispatch_type=draft.dispatch_type,
        target_label=target_label,
        execution_mode=execution_mode,
        reason_codes=draft.reason_codes,
        source_label=source_label,
        dispatch_summary=dispatch_summary,
    )

    return DispatchCommit(
        commit_id=dispatch_id,
        draft_id=draft.subject_id,
        dispatch_record=dispatch_record,
    )


def dispatch_commit_markdown(commit: DispatchCommit) -> str:
    """Render one dispatch commit as deterministic markdown."""

    lines = [
        "# Dispatch Commit",
        "",
        "## Commit ID",
        "",
        commit.commit_id,
        "",
        "## Draft",
        "",
        commit.draft_id,
        "",
        "## Dispatch Record",
        "",
        "```text",
        dispatch_record_markdown(commit.dispatch_record),
        "```",
        "",
        "## Provenance Notice",
        "",
        PROVENANCE_NOTICE,
        "",
    ]
    return "\n".join(lines)


def write_dispatch_commit(commit: DispatchCommit, repo_root: str | Path = ".") -> str:
    """Write deterministic derived dispatch-commit markdown."""

    root = Path(repo_root)
    output = root / ".leira" / "dispatch_commits" / f"{commit.commit_id}.commit.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dispatch_commit_markdown(commit), encoding="utf-8")
    return output.relative_to(root).as_posix()
