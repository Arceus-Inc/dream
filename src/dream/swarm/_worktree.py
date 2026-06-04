"""git worktree helpers for branch-isolated tasks (spec 01).

This module starts with the *security boundary*: a slug becomes a directory name
and a branch name, so it is validated before any filesystem or git operation.
The worktree lifecycle (create/resume/remove/cleanup) builds on these in a later
change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "WorktreeInfo",
    "WorktreeSlug",
    "flatten_slug",
    "validate_worktree_slug",
]

_VALID_SEGMENT = re.compile(r"^[a-zA-Z0-9._-]+$")
_MAX_SLUG_LENGTH = 64


def validate_worktree_slug(slug: str) -> str:
    """Validate a worktree slug; return it unchanged or raise ``ValueError``.

    A security boundary for *both* filesystem paths and git branch names (the
    slug becomes ``worktree-{flat-slug}``), so it enforces path-traversal *and*
    ``git check-ref-format`` constraints:

    - non-empty, at most 64 characters;
    - not an absolute path (no leading ``/`` or ``\\``);
    - each ``/``-separated segment matches ``[a-zA-Z0-9._-]+``;
    - no ``.`` or ``..`` segments (path traversal);
    - per git ref rules: no segment may start/end with ``.``, contain ``..``,
      or end with ``.lock``.
    """
    if not slug:
        raise ValueError("worktree slug must not be empty")

    if len(slug) > _MAX_SLUG_LENGTH:
        raise ValueError(
            f"worktree slug must be {_MAX_SLUG_LENGTH} characters or fewer (got {len(slug)})"
        )

    if slug.startswith(("/", "\\")):
        raise ValueError(f"worktree slug must not be an absolute path: {slug!r}")

    for segment in slug.split("/"):
        if not _VALID_SEGMENT.match(segment):
            raise ValueError(
                f"worktree slug {slug!r}: each segment must be non-empty and contain only "
                "letters, digits, dots, underscores, and dashes"
            )
        if (
            segment.startswith(".")
            or segment.endswith(".")
            or segment.endswith(".lock")
            or ".." in segment
        ):
            raise ValueError(
                f"worktree slug {slug!r}: segment {segment!r} is not a valid git ref component "
                '(no leading/trailing ".", no "..", no ".lock" suffix)'
            )

    return slug


def flatten_slug(slug: str) -> str:
    """Validate then flatten a slug for a flat layout: ``a/b`` -> ``a+b``.

    Validation runs here too, so a caller cannot bypass the security boundary by
    flattening an unvalidated slug straight into a directory name.
    """
    validate_worktree_slug(slug)
    return slug.replace("/", "+")


@dataclass(frozen=True)
class WorktreeSlug:
    """A validated slug. Constructing one *is* the security check.

    Downstream worktree operations take this type rather than a raw ``str``, so
    an unvalidated slug cannot reach a filesystem or git operation.
    """

    value: str

    def __post_init__(self) -> None:
        validate_worktree_slug(self.value)

    @property
    def flat(self) -> str:
        """Flat directory form: ``a/b`` -> ``a+b``."""
        return self.value.replace("/", "+")

    @property
    def branch(self) -> str:
        """The generated git branch name for this worktree."""
        return f"worktree-{self.flat}"


@dataclass(frozen=True)
class WorktreeInfo:
    """Metadata describing a managed git worktree."""

    slug: str
    path: Path
    branch: str
    original_path: Path
    created_at: float
    agent_id: str | None = None
