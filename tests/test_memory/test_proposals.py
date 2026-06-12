"""Tests for the outbound proposal seam (spec 11a; :mod:`dream.memory._proposals`).

dream proposes; it never promotes. These cover slug validation (the security
boundary — a hostile slug must never escape the proposals dir), the file shape,
and durability (proposals land in the home queue, not the worktree).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.memory import (
    InvalidSlugError,
    project_memory_dir,
    proposals_dir,
    validate_slug,
    write_proposal,
)


def test_proposals_dir_is_under_home_memory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    expected = project_memory_dir(home, repo) / "_proposals"
    assert proposals_dir(home, repo) == expected


def test_validate_slug_accepts_clean_slug() -> None:
    assert validate_slug("retry-policy") == "retry-policy"
    assert validate_slug("abc123") == "abc123"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "../escape",
        "a/b",
        "Has-Caps",
        "has space",
        "-leading",
        "trailing-",
        "under_score",
        "dot.slug",
    ],
)
def test_validate_slug_rejects_unsafe(bad: str) -> None:
    with pytest.raises(InvalidSlugError):
        validate_slug(bad)


def test_proposal_tool_creates_proposal_file(tmp_path: Path) -> None:
    out = write_proposal(
        tmp_path / "_proposals",
        slug="retry-policy",
        content="Always back off exponentially with jitter.",
        rationale="seen the same bug fixed three ways",
        source="session://s_abc",
    )
    assert out.exists()
    assert out.name.endswith("-retry-policy.md")
    body = out.read_text(encoding="utf-8")
    assert "slug: retry-policy" in body
    assert "source: session://s_abc" in body
    assert "Always back off exponentially with jitter." in body


def test_proposal_rationale_is_safe_yaml(tmp_path: Path) -> None:
    # A rationale with a colon and a quote must not break the frontmatter.
    out = write_proposal(
        tmp_path / "_proposals",
        slug="quoting",
        content="body",
        rationale='note: it said "hi" here',
        source="session://s_x",
    )
    body = out.read_text(encoding="utf-8")
    assert 'rationale: "note: it said \\"hi\\" here"' in body


def test_proposal_bad_slug_writes_nothing(tmp_path: Path) -> None:
    directory = tmp_path / "_proposals"
    with pytest.raises(InvalidSlugError):
        write_proposal(
            directory,
            slug="../escape",
            content="body",
            rationale="r",
            source="session://s_x",
        )
    assert not directory.exists()


def test_proposal_written_to_durable_home_not_worktree(tmp_path: Path) -> None:
    home = tmp_path / "home"
    worktree = tmp_path / "worktrees" / "task-1"
    out = write_proposal(
        proposals_dir(home, worktree),
        slug="durable",
        content="body",
        rationale="r",
        source="session://s_x",
    )
    # The proposal lives under the home, so tearing down the worktree leaves it.
    assert home in out.parents
    assert worktree not in out.parents
