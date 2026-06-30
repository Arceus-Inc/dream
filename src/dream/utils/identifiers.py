"""Shared path-segment validators for task IDs and sprint numbers.

Centralises the safety checks that ``sprint._checks``,
``planner._artefacts``, and ``config.paths`` each carried their own copy
of.  Every module that needs to embed a task ID or sprint number in a
filesystem path should import from here.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["checked_sprint_number", "checked_task_id"]


def checked_task_id(task_id: str) -> str:
    """Reject task IDs that could escape a sandbox root via path traversal.

    Covers POSIX separators, Windows drive/NTFS alternate-data-stream
    syntax (``:``), null bytes, and ``Path.is_absolute()`` as a
    catch-all.
    """
    if (
        not task_id
        or task_id in {".", ".."}
        or "/" in task_id
        or "\\" in task_id
        or ":" in task_id
        or "\x00" in task_id
        or Path(task_id).is_absolute()
    ):
        raise ValueError(f"unsafe task_id: {task_id!r}")
    return task_id


def checked_sprint_number(sprint_number: int) -> int:
    """Reject non-int or non-positive sprint numbers.

    ``bool`` is a subclass of ``int``; reject it so ``True``/``False``
    can't masquerade as sprint 1/0 and produce a
    ``sprint-True.json`` filename.
    """
    if not isinstance(sprint_number, int) or isinstance(sprint_number, bool):
        raise TypeError(
            f"sprint_number must be an int, got {type(sprint_number).__name__}"
        )
    if sprint_number < 1:
        raise ValueError(f"sprint_number must be >= 1, got {sprint_number}")
    return sprint_number
