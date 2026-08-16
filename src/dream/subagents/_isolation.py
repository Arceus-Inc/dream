"""Filesystem isolation mode for a subagent session."""

from __future__ import annotations

from enum import StrEnum


class IsolationMode(StrEnum):
    """Where a child session's tools run.

    ``SHARED`` — same worktree as the parent (Hermes default; cheap).
    ``WORKTREE`` — short-lived git worktree under the session scratch dir.
    The child's permission cwd is the worktree, so writes cannot escape into
    the parent tree. Command-bearing tools (bash / execute_code) are dropped
    because shell redirects cannot be confined. The checkout is force-removed
    after join: edits are ephemeral and never merge back. Use WORKTREE to
    confine side effects, not to land durable patches.
    """

    SHARED = "shared"
    WORKTREE = "worktree"


__all__ = ["IsolationMode"]
