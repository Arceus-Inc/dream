"""Outcome → ledger transition + tech-debt append.

Spec 10 §"Generator + evaluator loop" step 6:

- ``pass``           → step transitions to ``done`` (and advances).
- ``needs-changes``  → step stays ``in_progress``; items are surfaced into
  the next contract's negotiation log via
  :func:`dream.sprint.load_pending_carry_items`.
- ``fail``           → step transitions to ``blocked``; a tech-debt entry
  is appended to ``<wt>/docs/exec-plans/tech-debt-tracker.md``.

This module is pure transitions: no event emission, no contract writing.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dream.planner import LedgerStep, PlannerLedger
from dream.utils.file_lock import exclusive_file_lock
from dream.utils.fs import atomic_write_text

from ._contract import tech_debt_path
from ._evaluation import EvaluationRecord
from ._ledger_ops import replace_step_by_id

__all__ = ["append_tech_debt", "apply_outcome"]


def apply_outcome(ledger: PlannerLedger, record: EvaluationRecord) -> PlannerLedger:
    """Return a new ledger with ``record.step_id`` transitioned per the rules.

    Raises ``KeyError`` if the step id isn't present in the ledger.
    """
    def _transition(step: LedgerStep) -> LedgerStep:
        if step.status != "in_progress":
            raise ValueError(
                f"cannot apply outcome to step {record.step_id!r}: only an "
                f"in_progress step may transition, got status "
                f"{step.status!r}"
            )
        new_status: Literal["done", "blocked", "in_progress"]
        if record.outcome == "pass":
            new_status = "done"
        elif record.outcome == "fail":
            new_status = "blocked"
        else:  # needs-changes
            new_status = "in_progress"
        return replace(step, status=new_status)

    return replace_step_by_id(ledger, record.step_id, _transition)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def append_tech_debt(worktree_root: str | Path, record: EvaluationRecord) -> Path:
    """Append a markdown entry describing the ``fail`` to the tech-debt log.

    Refuses non-``fail`` records: the tracker is the inbox for blocked
    work, not a general audit log.
    """
    if record.outcome != "fail":
        raise ValueError(
            f"append_tech_debt accepts only outcome='fail' records, got {record.outcome!r}"
        )
    path = tech_debt_path(worktree_root)
    items_md = "\n".join(f"  - {item}" for item in record.items) if record.items else ""
    entry = (
        f"## {record.task_id} — sprint-{record.sprint_number} — step {record.step_id}\n"
        f"- recorded: {_now_iso()}\n"
        f"- notes: {record.notes}\n"
        + (f"- items:\n{items_md}\n" if items_md else "")
        + "\n"
    )
    # Serialize the read-modify-write so concurrent appenders can't both read
    # the same prior content and clobber one another (last-write-wins).
    lock_path = path.with_name(f"{path.name}.lock")
    with exclusive_file_lock(lock_path):
        prior = path.read_text(encoding="utf-8") if path.exists() else ""
        atomic_write_text(path, prior + entry)
    return path
