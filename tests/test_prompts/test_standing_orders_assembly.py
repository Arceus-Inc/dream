"""Workforce waist lives in core-beliefs.md standing orders (not a Base Prompt)."""

from __future__ import annotations

from pathlib import Path

from dream.prompts.system_prompt import assemble_session_system_prompt
from dream.services.core_beliefs import (
    extract_standing_orders,
    packaged_core_beliefs_path,
    resolve_core_beliefs_path,
)


def test_packaged_core_beliefs_carry_workforce_waist() -> None:
    orders = extract_standing_orders(packaged_core_beliefs_path())
    joined = "\n".join(orders.always)
    assert "You are an employee of a AI Workforce" in joined
    assert "todo_write" in joined
    assert "recall" in joined.lower() or "EPISODIC MEMORY" in joined
    assert "TOOL CHOICE" in joined
    assert any("force-push" in n.lower() for n in orders.never)


def test_resolve_falls_back_to_packaged_when_worktree_missing(tmp_path: Path) -> None:
    assert resolve_core_beliefs_path(tmp_path) == packaged_core_beliefs_path()


def test_resolve_prefers_worktree_file(tmp_path: Path) -> None:
    target = tmp_path / "docs" / "design-docs" / "core-beliefs.md"
    target.parent.mkdir(parents=True)
    target.write_text("## Standing orders\n\n- local only\n", encoding="utf-8")
    assert resolve_core_beliefs_path(tmp_path) == target


def test_assemble_injects_standing_orders_first() -> None:
    prompt = assemble_session_system_prompt(
        standing_orders_path=packaged_core_beliefs_path(),
        runtime_info="RUNTIME",
        catalogue="",
        memory_catalogue="",
        system_prompt="CRAFT BRIEF",
    )
    assert "You are an employee of a AI Workforce" in prompt
    assert prompt.index("Standing orders") < prompt.index("RUNTIME")
    assert prompt.index("RUNTIME") < prompt.index("CRAFT BRIEF")
