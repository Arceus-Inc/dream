"""Populate compaction carryover from durable workspace state (Wave C).

Reads only paths that already exist on disk — exec-plan ledger under
``docs/exec-plans/active/``, no new subsystems. Attachments after full
compact consume the keys ``_attachments`` documents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dream.planner._artefacts import PlannerLedger


def refresh_carryover_from_workspace(
    working_dir: Path | None,
    carryover: dict[str, Any] | None,
) -> None:
    """Merge known live state into ``carryover`` in place."""
    if working_dir is None or carryover is None:
        return

    _merge_exec_plan(working_dir, carryover)


def _merge_exec_plan(working_dir: Path, carryover: dict[str, Any]) -> None:
    active = working_dir / "docs" / "exec-plans" / "active"
    if not active.is_dir():
        return

    json_files = sorted(active.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not json_files:
        return

    ledger_path = json_files[0]
    try:
        ledger = PlannerLedger.load(ledger_path)
    except (OSError, ValueError, KeyError, TypeError):
        return

    carryover["exec_plan_filename"] = ledger_path.name

    current: str | None = None
    for step in ledger.steps:
        if step.status == "in_progress":
            current = step.id
            break
    if current is None:
        for step in ledger.steps:
            if step.status == "pending":
                current = step.id
                break
    if current is not None:
        carryover["exec_plan_current_step"] = current

    blocked = [
        {"step_id": step.id, "reason": step.notes or "blocked"}
        for step in ledger.steps
        if step.status == "blocked"
    ]
    if blocked:
        carryover["blocked_steps"] = blocked


__all__ = ["refresh_carryover_from_workspace"]
