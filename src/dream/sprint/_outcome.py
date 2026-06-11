"""Outcome → ledger transition + tech-debt append.

Spec 10 §"Generator + evaluator loop" step 6:

- ``pass``           → step transitions to ``done`` (and advances).
- ``needs-changes``  → step stays ``in_progress``; evaluator notes are
  accumulated on the step so the generator retries with context rather
  than an identical prompt.  After ``NEEDS_CHANGES_LIMIT`` consecutive
  rejections the step transitions to ``blocked`` instead, preventing the
  sprint budget from being burned on a structurally blocked step.
- ``fail``           → step transitions to ``blocked``; evaluator notes
  are also carried onto the step (the blocked reason is then readable
  without opening the tech-debt file); a tech-debt entry is appended to
  ``<wt>/docs/exec-plans/tech-debt-tracker.md``.

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

# Maximum number of ``needs-changes`` evaluations before a step is escalated
# to ``blocked`` to avoid burning the entire sprint budget on a structurally
# impossible step.
NEEDS_CHANGES_LIMIT: int = 2


def _append_evaluator_notes(
    prior: str,
    record_notes: str,
    sprint_number: int,
) -> str:
    """Return ``prior`` with the evaluator's feedback appended.

    Appends nothing when ``record_notes`` is empty — avoids adding a bare
    separator or an empty evaluator tag that would pollute the notes field.
    """
    if not record_notes:
        return prior
    tag = f"[evaluator, sprint {sprint_number}] {record_notes}"
    if prior:
        return f"{prior}\n{tag}"
    return tag


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
        if record.outcome == "pass":
            return replace(step, status="done")
        if record.outcome == "fail":
            # Carry the evaluator's notes so the blocked reason is readable
            # inline without consulting the tech-debt file.
            new_notes = _append_evaluator_notes(
                step.notes, record.notes, record.sprint_number
            )
            return replace(step, status="blocked", notes=new_notes)
        # needs-changes: accumulate notes and count; escalate when limit hit.
        new_count = step.needs_changes_count + 1
        new_notes = _append_evaluator_notes(
            step.notes, record.notes, record.sprint_number
        )
        new_status: Literal["blocked", "in_progress"] = (
            "blocked" if new_count >= NEEDS_CHANGES_LIMIT else "in_progress"
        )
        return replace(
            step,
            status=new_status,
            notes=new_notes,
            needs_changes_count=new_count,
        )

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
