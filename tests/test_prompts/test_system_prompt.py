"""Prompt assembly stays deterministic and package-contained."""

from __future__ import annotations

from dataclasses import is_dataclass
from pathlib import Path

from dream.prompts.system_prompt import (
    ContextPromptBlock,
    RolePromptBlock,
    RuntimeContextBlock,
    StablePromptBlock,
    assemble_session_system_prompt,
    load_agents_md,
    packaged_standing_orders,
)


def test_assembly_orders_explicit_blocks_with_a_stable_prefix() -> None:
    prompt = assemble_session_system_prompt(
        stable=StablePromptBlock(role="generator"),
        context=ContextPromptBlock(
            workspace_governance="GOVERNANCE",
            skill_catalogue="SKILLS",
            memory_catalogue="MEMORY",
            agents_md="I am the craft employee.",
        ),
        role=RolePromptBlock(instructions="ADDENDUM"),
    )

    assert prompt.index("<stable>") < prompt.index("<context>") < prompt.index("<role>")
    assert prompt.index("AGENTS.md") < prompt.index("GOVERNANCE") < prompt.index("SKILLS")
    assert "I am the craft employee." in prompt
    assert "ADDENDUM" in prompt


def test_stable_prefix_is_byte_identical_for_same_role() -> None:
    stable = StablePromptBlock(role="planner")

    assert stable.render().encode() == StablePromptBlock(role="planner").render().encode()
    assert is_dataclass(stable)
    assert stable.__dataclass_params__.frozen


def test_phase_chapter_selected_by_role() -> None:
    planner = packaged_standing_orders(role="planner")
    evaluator = packaged_standing_orders(role="evaluator")

    assert "Planner phase" in planner
    assert "Evaluator phase" in evaluator
    assert "Planner phase" not in evaluator
    assert "Evaluator phase" not in planner
    assert "Tool choice" in planner
    assert "Tool choice" in evaluator


def test_unknown_role_keeps_common_only() -> None:
    orders = packaged_standing_orders(role="scout")

    assert "Dream standing orders" in orders
    assert "Planner phase" not in orders
    assert "You are the planner" not in orders


def test_context_or_addendum_changes_do_not_change_the_stable_prefix() -> None:
    stable = StablePromptBlock(role="generator")
    first = assemble_session_system_prompt(
        stable=stable,
        context=ContextPromptBlock(
            workspace_governance="FIRST GOVERNANCE",
            skill_catalogue="SKILLS",
            memory_catalogue="",
            agents_md="BRIEF A",
        ),
        role=RolePromptBlock(instructions="ROLE"),
    )
    second = assemble_session_system_prompt(
        stable=stable,
        context=ContextPromptBlock(
            workspace_governance="SECOND GOVERNANCE",
            skill_catalogue="OTHER SKILLS",
            memory_catalogue="MEMORY",
            agents_md="BRIEF B",
        ),
        role=RolePromptBlock(instructions="OTHER ROLE"),
    )

    stable_prefix = stable.render()
    assert first.startswith(stable_prefix)
    assert second.startswith(stable_prefix)


def test_packaged_standing_orders_are_available_without_workspace_docs() -> None:
    orders = packaged_standing_orders()

    assert "Dream" in orders
    assert "recall" not in orders.lower()
    assert "get_run" not in orders


def test_agents_md_loads_harness_then_cwd(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("cwd brief", encoding="utf-8")
    assert load_agents_md(tmp_path) == "cwd brief"

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "AGENTS.md").write_text("harness brief", encoding="utf-8")
    assert load_agents_md(tmp_path) == "harness brief"


def test_agents_md_lives_in_context_not_stable() -> None:
    prompt = assemble_session_system_prompt(
        stable=StablePromptBlock(role="planner"),
        context=ContextPromptBlock(
            workspace_governance="",
            skill_catalogue="",
            memory_catalogue="",
            agents_md="Employee identity only.",
        ),
        role=RolePromptBlock(instructions=None),
    )
    stable = StablePromptBlock(role="planner").render()

    assert "Employee identity only." in prompt
    assert "Employee identity only." not in stable
    assert prompt.index("<stable>") < prompt.index("Employee identity only.")


def test_replace_mode_omits_packaged_standing_orders() -> None:
    prompt = assemble_session_system_prompt(
        stable=StablePromptBlock(role="planner", include=False),
        context=ContextPromptBlock(
            workspace_governance="GOV",
            skill_catalogue="",
            memory_catalogue="",
        ),
        role=RolePromptBlock(instructions="CUSTOM ROLE ONLY"),
    )

    assert "<stable>" not in prompt
    assert "Planner phase" not in prompt
    assert "Dream standing orders" not in prompt
    assert "CUSTOM ROLE ONLY" in prompt
    assert "GOV" in prompt


def test_runtime_facts_are_a_user_context_block_not_system_prompt_content() -> None:
    runtime_context = RuntimeContextBlock(runtime_info="RUNTIME")
    prompt = assemble_session_system_prompt(
        stable=StablePromptBlock(),
        context=ContextPromptBlock(
            workspace_governance="",
            skill_catalogue="",
            memory_catalogue="",
        ),
        role=RolePromptBlock(instructions=None),
    )

    assert "RUNTIME" in runtime_context.render()
    assert "RUNTIME" not in prompt
    assert "<context>" not in prompt
    assert "<role>" not in prompt
