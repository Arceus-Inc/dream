"""Worktree-scoped directory layout for swarm IPC.

Spec 10 pins every swarm artefact to the worktree (``.harness/swarm/{leader}/...``)
rather than a global home directory. The deliberate divergence from
OpenHarness's home-directory mailbox is so a swarm message can be inspected
and committed alongside the worker's next change — the spec-00 "repo is the
system of record" rule.

A leader id becomes a directory name, so it is validated here (the same
security boundary that ``_worktree.validate_worktree_slug`` enforces for
worktree slugs, but with a simpler shape: no nested ``/``).
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "leader_inbox_dir",
    "leader_permissions_dir",
    "leader_swarm_dir",
    "validate_leader_id",
]


_VALID_LEADER_ID = re.compile(r"^[a-zA-Z0-9._-]+$")
_MAX_LEADER_ID_LENGTH = 64


def validate_leader_id(leader_id: str) -> str:
    """Validate a leader id; return it unchanged or raise ``ValueError``.

    The id becomes a single directory segment under ``.harness/swarm/``,
    so we enforce:

    - non-empty, at most 64 characters;
    - matches ``[a-zA-Z0-9._-]+`` (no path separators, no whitespace,
      no shell metacharacters);
    - no ``.`` / ``..`` traversal segments.
    """
    if not leader_id:
        raise ValueError("leader id must not be empty")
    if len(leader_id) > _MAX_LEADER_ID_LENGTH:
        raise ValueError(
            f"leader id must be {_MAX_LEADER_ID_LENGTH} characters or fewer "
            f"(got {len(leader_id)})"
        )
    if not _VALID_LEADER_ID.match(leader_id):
        raise ValueError(
            f"leader id {leader_id!r}: must contain only letters, digits, "
            "dots, underscores, and dashes (no path separators)"
        )
    if leader_id in {".", ".."}:
        raise ValueError(f"leader id {leader_id!r}: traversal segment not allowed")
    return leader_id


def leader_swarm_dir(worktree_root: Path, leader_id: str) -> Path:
    """Return ``<worktree_root>/.harness/swarm/<leader_id>`` (path only, no mkdir)."""
    return Path(worktree_root) / ".harness" / "swarm" / validate_leader_id(leader_id)


def leader_inbox_dir(worktree_root: Path, leader_id: str) -> Path:
    """Return the leader's inbox directory path (no mkdir)."""
    return leader_swarm_dir(worktree_root, leader_id) / "inbox"


def leader_permissions_dir(worktree_root: Path, leader_id: str) -> Path:
    """Return the leader's permissions directory path (no mkdir)."""
    return leader_swarm_dir(worktree_root, leader_id) / "permissions"
