"""Filesystem isolation mode for a subagent session."""

from __future__ import annotations

from enum import StrEnum


class IsolationMode(StrEnum):
    """Where a child session's tools run.

    ``SHARED`` — same worktree as the parent (Hermes default; cheap).
    ``WORKTREE`` — short-lived git worktree under scratch; torn down after join.
    """

    SHARED = "shared"
    WORKTREE = "worktree"


__all__ = ["IsolationMode"]
