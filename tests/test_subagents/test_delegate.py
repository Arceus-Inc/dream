"""TDD — Hermes-style child prompt + summary budget (Wave B)."""

from __future__ import annotations

from dream.subagents._delegate import (
    DEFAULT_MAX_SUMMARY_CHARS,
    apply_summary_budget,
    build_child_prompt,
)


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
