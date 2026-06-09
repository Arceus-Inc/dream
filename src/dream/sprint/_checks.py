"""Shared task_id / sprint_number validation for the sprint package.

Mirrors :func:`dream.planner._artefacts._checked_task_id` (the planner
owns its own copy to keep the public module surface minimal). Centralising
here would mean either importing a private symbol or widening the planner
surface — both worse than a small duplicate.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["checked_sprint_number", "checked_task_id"]


def checked_task_id(task_id: str) -> str:
    if (
        not task_id
        or task_id in {".", ".."}
        or "/" in task_id
        or "\\" in task_id
        or ":" in task_id  # Windows drive / NTFS alternate-data-stream syntax
        or "\x00" in task_id
        or Path(task_id).is_absolute()
    ):
        raise ValueError(f"unsafe task_id: {task_id!r}")
    return task_id


def checked_sprint_number(sprint_number: int) -> int:
    # ``bool`` is a subclass of ``int``; reject it so ``True``/``False`` can't
    # masquerade as sprint 1/0 and produce a ``sprint-True.json`` filename.
    if not isinstance(sprint_number, int) or isinstance(sprint_number, bool):
        raise TypeError(
            f"sprint_number must be an int, got {type(sprint_number).__name__}"
        )
    if sprint_number < 1:
        raise ValueError(f"sprint_number must be >= 1, got {sprint_number}")
    return sprint_number
