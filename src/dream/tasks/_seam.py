"""Spec 07 slice 2 — durable↔ephemeral seam.

A :class:`~dream.tasks._manager.BackgroundTaskManager` task is ephemeral;
the :class:`~dream.tasks._ledger.Ledger` is durable. The seam is a
**completion listener** that, when a task tagged with a ledger reference
reaches a terminal state, updates the corresponding ledger entry and
commits the JSON back to disk via the atomic write helper.

Spec 07 §"Completion → durable update (the seam)":

    A registered ``CompletionListener`` receives the terminal
    ``TaskRecord``. If the task was advancing a ledger entry, the
    listener updates that entry's ``status``/``passes``/``notes`` and
    commits the ledger.

Tagging convention (carried in :attr:`TaskRecord.metadata`):

- ``task_id`` — the exec-plan task id (also redundant context for logs).
- ``entry_id`` — which ``LedgerEntry`` this run is advancing.
- ``ledger_path`` — absolute path to the ledger JSON file.

Untagged tasks are a silent no-op for the listener — most background
shell tasks aren't tied to a ledger.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dream.tasks._ledger import Ledger, read_ledger, write_ledger
from dream.tasks._manager import CompletionListener
from dream.tasks._types import TaskRecord

__all__ = ["make_ledger_completion_listener"]


def _terminal_outcome(task: TaskRecord) -> tuple[str, str]:
    """Map a terminal task to (next_entry_status, note).

    - return_code == 0 → done
    - non-zero / killed → blocked
    """
    if task.status == "completed" and task.return_code == 0:
        return "done", f"completed (task {task.id})"
    if task.status == "killed":
        return "blocked", f"killed (task {task.id})"
    rc = task.return_code if task.return_code is not None else "?"
    return "blocked", f"failed (task {task.id}, return_code={rc})"


def make_ledger_completion_listener() -> CompletionListener:
    """Build a completion listener that updates the tagged ledger entry.

    The listener is synchronous (no I/O wait), since the read-modify-write
    on the ledger is local-disk-bound and small. Atomic writes are handled
    inside ``write_ledger``.
    """

    def listener(task: TaskRecord) -> None:
        meta = task.metadata
        ledger_path = meta.get("ledger_path")
        entry_id = meta.get("entry_id")
        if not ledger_path or not entry_id:
            return  # untagged — not seam business

        ledger: Ledger = read_ledger(ledger_path)
        next_status, note = _terminal_outcome(task)
        now = datetime.now(UTC)

        # Append the note first so it's recorded even if mark_done rejects
        # under evaluator_enabled (we don't have evaluator evidence here).
        ledger = ledger.append_note(entry_id=entry_id, note=note)
        if next_status == "done":
            # passes=False because slice 2 has no evaluator signal; the
            # evaluator pass (#12) populates passes in a separate step.
            try:
                ledger = ledger.mark_done(entry_id=entry_id, passes=False, now=now)
            except Exception:
                # evaluator_enabled blocks done-without-passes — fall back
                # to blocked so the entry isn't left dangling in_progress.
                ledger = ledger.mark_blocked(entry_id=entry_id, now=now)
        else:
            ledger = ledger.mark_blocked(entry_id=entry_id, now=now)

        write_ledger(ledger_path, ledger)

    return listener
