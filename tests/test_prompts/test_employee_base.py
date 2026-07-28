"""Workforce Base Prompt — Hermes-style identity + tool-gated directives."""

from __future__ import annotations

from dream.prompts.employee_base import (
    EMPLOYEE_BASE_PROMPT,
    EMPLOYEE_MODE_METADATA_KEY,
    RECALL_DIRECTIVE,
    RESUME_DIRECTIVE,
    TOOL_CHOICE_MATRIX,
    render_employee_base_prompt,
    should_inject_employee_base,
)
from dream.prompts.system_prompt import assemble_session_system_prompt


def test_base_prompt_starts_with_workforce_identity() -> None:
    assert EMPLOYEE_BASE_PROMPT.startswith("You are an employee of a AI Workforce")


def test_toolless_session_gets_identity_only() -> None:
    text = render_employee_base_prompt(tool_names=())
    assert text == EMPLOYEE_BASE_PROMPT
    assert RESUME_DIRECTIVE not in text
    assert RECALL_DIRECTIVE not in text
    assert TOOL_CHOICE_MATRIX not in text


def test_generator_tools_gate_resume_recall_and_matrix() -> None:
    text = render_employee_base_prompt(
        tool_names=("todo_write", "recall", "skill", "spawn_subagent", "read_file")
    )
    assert EMPLOYEE_BASE_PROMPT in text
    assert RESUME_DIRECTIVE in text
    assert RECALL_DIRECTIVE in text
    assert TOOL_CHOICE_MATRIX in text


def test_should_inject_respects_metadata_and_skips_subagents() -> None:
    assert should_inject_employee_base(
        employee_mode=True,
        metadata=None,
        system_prompt=None,
        is_subagent=False,
    )
    assert not should_inject_employee_base(
        employee_mode=True,
        metadata=None,
        system_prompt=None,
        is_subagent=True,
    )
    assert not should_inject_employee_base(
        employee_mode=True,
        metadata={EMPLOYEE_MODE_METADATA_KEY: False},
        system_prompt="## Operating brief\ncraft",
        is_subagent=False,
    )
    assert should_inject_employee_base(
        employee_mode=False,
        metadata=None,
        system_prompt="You are the generator.\n\n## Operating brief (your role in the org)\nBex",
        is_subagent=False,
    )


def test_assemble_places_base_before_runtime_and_craft(tmp_path) -> None:
    beliefs = tmp_path / "core-beliefs.md"
    beliefs.write_text(
        "## Standing orders\n\n- Be kind\n\n## What we don't do\n\n- Lie\n",
        encoding="utf-8",
    )
    prompt = assemble_session_system_prompt(
        standing_orders_path=beliefs,
        runtime_info="RUNTIME",
        catalogue="",
        memory_catalogue="",
        system_prompt="CRAFT BRIEF",
        employee_mode=True,
        tool_names=frozenset({"todo_write", "skill"}),
    )
    assert "Standing orders" in prompt
    assert "You are an employee of a AI Workforce" in prompt
    assert prompt.index("Standing orders") < prompt.index("You are an employee of a AI Workforce")
    assert prompt.index("You are an employee of a AI Workforce") < prompt.index("RUNTIME")
    assert prompt.index("RUNTIME") < prompt.index("CRAFT BRIEF")
    assert "RESUME, DON'T RESTART" in prompt
    assert TOOL_CHOICE_MATRIX in prompt
