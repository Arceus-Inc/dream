"""Prompt assembly stays deterministic and package-contained."""

from __future__ import annotations

from dataclasses import is_dataclass

from dream.prompts.system_prompt import (
    ContextPromptBlock,
    RolePromptBlock,
    RuntimeContextBlock,
    StablePromptBlock,
    assemble_session_system_prompt,
    packaged_standing_orders,
)


def test_assembly_orders_explicit_blocks_with_a_stable_prefix() -> None:
    prompt = assemble_session_system_prompt(
        stable=StablePromptBlock(),
        context=ContextPromptBlock(
            workspace_governance="GOVERNANCE",
            skill_catalogue="SKILLS",
            memory_catalogue="MEMORY",
        ),
        role=RolePromptBlock(instructions="ROLE"),
    )

    assert prompt.index("<stable>") < prompt.index("<context>") < prompt.index("<role>")
    assert prompt.index("GOVERNANCE") < prompt.index("SKILLS") < prompt.index("ROLE")


def test_stable_prefix_is_byte_identical_and_value_objects_are_immutable() -> None:
    stable = StablePromptBlock()

    assert stable.render().encode() == StablePromptBlock().render().encode()
    assert is_dataclass(stable)
    assert stable.__dataclass_params__.frozen


def test_context_or_role_changes_do_not_change_the_stable_prefix() -> None:
    stable = StablePromptBlock()
    first = assemble_session_system_prompt(
        stable=stable,
        context=ContextPromptBlock(
            workspace_governance="FIRST GOVERNANCE",
            skill_catalogue="SKILLS",
            memory_catalogue="",
        ),
        role=RolePromptBlock(instructions="ROLE"),
    )
    second = assemble_session_system_prompt(
        stable=stable,
        context=ContextPromptBlock(
            workspace_governance="SECOND GOVERNANCE",
            skill_catalogue="OTHER SKILLS",
            memory_catalogue="MEMORY",
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
