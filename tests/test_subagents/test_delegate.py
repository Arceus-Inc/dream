"""TDD — Hermes-style child prompt + summary budget (Wave B)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from dream.subagents._declaration import Subagent
from dream.subagents._delegate import (
    DEFAULT_MAX_SUMMARY_CHARS,
    apply_summary_budget,
    build_child_prompt,
    run_subagent_delegate,
)
from dream.subagents._projection import SubagentResult


def test_build_child_prompt_contains_goal_and_context() -> None:
    text = build_child_prompt("review the diff", "files: a.py, b.py", workspace_path="/tmp/wt")
    assert "YOUR TASK:" in text
    assert "review the diff" in text
    assert "CONTEXT:" in text
    assert "files: a.py, b.py" in text
    assert "WORKSPACE PATH: /tmp/wt" in text
    # Firewall: no claim of parent history.
    assert "parent transcript" not in text.lower()


def test_build_child_prompt_omits_empty_context() -> None:
    text = build_child_prompt("do the thing")
    assert "YOUR TASK:" in text
    assert "CONTEXT:" not in text


def test_apply_summary_budget_noop_when_short() -> None:
    short = "ok"
    assert apply_summary_budget(short, max_chars=100) == short


def test_apply_summary_budget_truncates() -> None:
    long = "A" * 10_000 + "MID" + "B" * 10_000
    out = apply_summary_budget(long, max_chars=500)
    assert len(out) <= 500
    assert "truncated" in out
    assert out.startswith("A")
    assert out.endswith("B")


def test_default_budget_constant() -> None:
    assert DEFAULT_MAX_SUMMARY_CHARS == 24_000


async def test_over_budget_summary_spills_to_scratch_not_the_worktree(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    scratch = tmp_path / "scratch"
    agent = Subagent(name="critic", description="reviews", tools=("read_file",))
    full = "A" * 5_000
    inline = SubagentResult(name="critic", output=full, success=True)

    with patch(
        "dream.subagents._delegate.run_subagent_session",
        new_callable=AsyncMock,
        return_value=inline,
    ):
        result = await run_subagent_delegate(
            agent,
            goal="review",
            harness=AsyncMock(),
            working_dir=worktree,
            spill_dir=scratch,
            summary_budget=500,
        )

    spilled = list((scratch / "delegation").iterdir())
    assert len(spilled) == 1
    assert spilled[0].read_text(encoding="utf-8") == full
    assert str(spilled[0]) in result.output
    assert not list(worktree.iterdir())


async def test_over_budget_summary_without_scratch_does_not_write(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    agent = Subagent(name="critic", description="reviews", tools=("read_file",))
    inline = SubagentResult(name="critic", output="A" * 5_000, success=True)

    with patch(
        "dream.subagents._delegate.run_subagent_session",
        new_callable=AsyncMock,
        return_value=inline,
    ):
        result = await run_subagent_delegate(
            agent,
            goal="review",
            harness=AsyncMock(),
            working_dir=worktree,
            summary_budget=500,
        )

    assert "spilled to" not in result.output
    assert not list(worktree.iterdir())
