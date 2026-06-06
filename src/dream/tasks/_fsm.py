"""Spec 07 slice 1 — plan FSM and on-disk layout.

Plans live under ``docs/exec-plans/{state}/`` where ``state`` is one of
:data:`PLAN_STATES`. The FSM is strict: ``draft → active → completed →
archived``, no jumps, no reversals (Spec 07 decision 4). Transitions move
the ``.md`` + ``.json`` pair between directories — *moved, never deleted*.

The retention helper finds completed plans older than ``retention_days``
(default 90 — Spec 07 decision 4); the ``doc-garden`` cron kind (slice 3)
will call ``move_plan(..., to_state="archived")`` for each.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from dream.tasks._ledger import LedgerState
from dream.tasks._plan import ExecPlan, read_plan, write_plan

__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "PLAN_STATES",
    "PlanFSMError",
    "advance_state",
    "archive_candidates",
    "move_plan",
    "plan_dir",
]


PLAN_STATES: tuple[LedgerState, ...] = ("draft", "active", "completed", "archived")
_NEXT_STATE: dict[LedgerState, LedgerState] = {
    "draft": "active",
    "active": "completed",
    "completed": "archived",
}

DEFAULT_RETENTION_DAYS = 90


class PlanFSMError(ValueError):
    """An illegal plan transition was attempted."""


def advance_state(state: LedgerState) -> LedgerState:
    """Return the next legal state, or raise for the terminal ``archived``."""
    try:
        return _NEXT_STATE[state]
    except KeyError as exc:
        raise PlanFSMError(
            f"no transition out of terminal state: {state!r}"
        ) from exc


def plan_dir(root: str | Path, *, state: LedgerState) -> Path:
    """Return ``{root}/{state}/`` — the directory plans in ``state`` live in."""
    return Path(root) / state


def move_plan(
    root: str | Path,
    *,
    task_id: str,
    from_state: LedgerState,
    to_state: Literal["active", "completed", "archived"],
) -> ExecPlan:
    """Move a plan from ``from_state`` to ``to_state`` and update the ledger
    state to match.

    Only legal transitions (``draft→active``, ``active→completed``,
    ``completed→archived``) are accepted. The plan is read, the ledger's
    ``state`` is rewritten, the new pair is written to the destination dir,
    then the source pair is removed — *moved, never deleted* applies to the
    payload, not to the now-empty old path.
    """
    expected_next = _NEXT_STATE.get(from_state)
    if expected_next != to_state:
        raise PlanFSMError(
            f"illegal transition: {from_state!r} → {to_state!r} "
            f"(expected → {expected_next!r})"
        )
    src = plan_dir(root, state=from_state)
    dst = plan_dir(root, state=to_state)
    plan = read_plan(src, task_id=task_id)
    new_ledger = plan.ledger.model_copy(update={"state": to_state})
    moved = ExecPlan(task_id=plan.task_id, sections=dict(plan.sections), ledger=new_ledger)
    write_plan(dst, moved)
    # Source pair is now safely redundant — drop it. (The payload survives
    # in ``dst``; "moved, never deleted" applies to the plan, not the
    # filesystem locations.)
    (src / f"{task_id}.md").unlink()
    (src / f"{task_id}.json").unlink()
    return moved


def archive_candidates(
    root: str | Path,
    *,
    now: datetime,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> tuple[ExecPlan, ...]:
    """Return completed plans older than ``retention_days``.

    "Older than" is measured against ``Ledger.created_at`` — the plan's
    *birth* time, not its last update. That makes the retention window
    independent of late-life notes/edits and keeps the rule simple to
    explain to operators.
    """
    completed = plan_dir(root, state="completed")
    if not completed.is_dir():
        return ()
    cutoff = now - timedelta(days=retention_days)
    out: list[ExecPlan] = []
    for json_path in sorted(completed.glob("*.json")):
        task_id = json_path.stem
        try:
            plan = read_plan(completed, task_id=task_id)
        except (FileNotFoundError, ValueError):
            continue  # half-written pair; skip
        if plan.ledger.created_at <= cutoff:
            out.append(plan)
    return tuple(out)
