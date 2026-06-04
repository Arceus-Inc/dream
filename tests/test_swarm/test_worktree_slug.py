"""Spec 01 — worktree slug validation (security boundary) and flattening.

`validate_worktree_slug` is a security boundary, not a nicety: a task-id becomes
a directory name, so traversal/absolute/out-of-charset slugs must be rejected
before they ever reach the filesystem. `flatten_slug` keeps the worktree layout
flat by mapping ``/`` -> ``+``.
"""

from __future__ import annotations

import pytest

from dream.swarm._worktree import flatten_slug, validate_worktree_slug


def test_validate_slug_accepts_valid_simple() -> None:
    assert validate_worktree_slug("task-123") == "task-123"


def test_validate_slug_accepts_nested_segments() -> None:
    assert validate_worktree_slug("feature/login.v2_3") == "feature/login.v2_3"


def test_validate_slug_returns_input_unchanged() -> None:
    assert validate_worktree_slug("A.b-c_9") == "A.b-c_9"


def test_validate_slug_rejects_empty() -> None:
    with pytest.raises(ValueError):
        validate_worktree_slug("")


def test_validate_slug_rejects_too_long() -> None:
    with pytest.raises(ValueError):
        validate_worktree_slug("a" * 65)


def test_validate_slug_accepts_max_length() -> None:
    slug = "a" * 64
    assert validate_worktree_slug(slug) == slug


@pytest.mark.parametrize("slug", ["/abs", "\\abs", "/etc/passwd"])
def test_validate_slug_rejects_absolute(slug: str) -> None:
    with pytest.raises(ValueError):
        validate_worktree_slug(slug)


@pytest.mark.parametrize("slug", [".", "..", "../escape", "a/../b", "a/.."])
def test_validate_slug_rejects_dot_segments(slug: str) -> None:
    with pytest.raises(ValueError):
        validate_worktree_slug(slug)


@pytest.mark.parametrize("slug", ["has space", "weird$char", "a//b", "tab\there", "a/b/"])
def test_validate_slug_rejects_bad_charset(slug: str) -> None:
    with pytest.raises(ValueError):
        validate_worktree_slug(slug)


def test_flatten_slug_replaces_slash_with_plus() -> None:
    assert flatten_slug("feature/login/v2") == "feature+login+v2"


def test_flatten_slug_noop_without_slash() -> None:
    assert flatten_slug("task-123") == "task-123"
