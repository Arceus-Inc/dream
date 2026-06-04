"""git worktree helpers for branch-isolated tasks (spec 01).

This module starts with the *security boundary*: a slug becomes a directory name
and a branch name, so it is validated before any filesystem or git operation.
The worktree lifecycle (create/resume/remove/cleanup) builds on these in a later
change.
"""

from __future__ import annotations

import re

__all__ = ["flatten_slug", "validate_worktree_slug"]

_VALID_SEGMENT = re.compile(r"^[a-zA-Z0-9._-]+$")
_MAX_SLUG_LENGTH = 64


def validate_worktree_slug(slug: str) -> str:
    """Validate a worktree slug; return it unchanged or raise ``ValueError``.

    Rules (a security boundary, not a nicety):
    - non-empty, at most 64 characters;
    - not an absolute path (no leading ``/`` or ``\\``);
    - each ``/``-separated segment matches ``[a-zA-Z0-9._-]+``;
    - no ``.`` or ``..`` segments (path traversal).
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
        if segment in (".", ".."):
            raise ValueError(f'worktree slug {slug!r}: "." and ".." segments are not allowed')
        if not _VALID_SEGMENT.match(segment):
            raise ValueError(
                f"worktree slug {slug!r}: each segment must be non-empty and contain only "
                "letters, digits, dots, underscores, and dashes"
            )

    return slug


def flatten_slug(slug: str) -> str:
    """Flatten a slug for a flat directory layout: ``a/b`` -> ``a+b``."""
    return slug.replace("/", "+")
