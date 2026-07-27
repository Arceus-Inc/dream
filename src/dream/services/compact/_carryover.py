"""Populate compaction carryover from durable workspace state (Wave C).

Reads only paths that already exist on disk — exec-plan ledger under
``docs/exec-plans/active/``, no new subsystems.
"""

from __future__ import annotations

from pathlib import Path

from dream.planner._artefacts import LedgerStep, PlannerLedger
from dream.services.compact._carryover_state import BlockedStepEntry, CarryoverMetadata

_EXEC_STEP_PRIORITY = ("in_progress", "pending")


def refresh_carryover_from_workspace(
    working_dir: Path | None,
    carryover: CarryoverMetadata | None,
) -> None:
    """Merge known live state into ``carryover`` in place."""
    if working_dir is None or carryover is None:
        return

    _merge_exec_plan(working_dir, carryover)


def _current_exec_step(steps: tuple[LedgerStep, ...]) -> str | None:
    for status in _EXEC_STEP_PRIORITY:
        for step in steps:
            if step.status == status:
                return step.id
    return None


def _merge_exec_plan(working_dir: Path, carryover: CarryoverMetadata) -> None:
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

    blocked = [
        BlockedStepEntry(step_id=step.id, reason=step.notes or "blocked")
        for step in ledger.steps
        if step.status == "blocked"
    ]
    carryover.merge_exec_plan(
        filename=ledger_path.name,
        current_step=_current_exec_step(ledger.steps),
        blocked=blocked,
    )


__all__ = ["refresh_carryover_from_workspace"]
