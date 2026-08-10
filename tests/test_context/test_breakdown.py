"""Context pie estimates stay deterministic and category-complete."""

from __future__ import annotations

from dream.context import (
    ContextCategory,
    compute_context_breakdown,
    format_context_breakdown,
    render_context_command,
)


def test_breakdown_splits_stable_context_tools_and_conversation() -> None:
    system = (
        "<stable>\nSTANDING\n</stable>\n\n"
        "<context>\n# AGENTS.md\n\nI am the PM.\n\n"
        "# Available Skills\n\n- skill: foo\n\n"
        "# Memory catalogue\n\n- memory: bar\n</context>"
    )
    tools = [
        {
            "type": "function",
            "function": {"name": "read_file", "description": "r", "parameters": {}},
        },
        {
            "type": "function",
            "function": {"name": "mcp_github__list", "description": "m", "parameters": {}},
        },
        {
            "type": "function",
            "function": {"name": "spawn_subagent", "description": "s", "parameters": {}},
        },
    ]
    messages = [
        {"role": "user", "content": "hello world " * 20},
        {
            "role": "user",
            "content": "[Compaction summary — reference only]\nEarlier work done.",
        },
    ]

    breakdown = compute_context_breakdown(
        system_prompt=system,
        tools=tools,
        messages=messages,
        context_window=200_000,
    )

    assert breakdown.categories[ContextCategory.SYSTEM_PROMPT] > 0
    assert breakdown.categories[ContextCategory.RULES] > 0
    assert breakdown.categories[ContextCategory.SKILLS] > 0
    assert breakdown.categories[ContextCategory.MEMORY] > 0
    assert breakdown.categories[ContextCategory.TOOL_DEFINITIONS] > 0
    assert breakdown.categories[ContextCategory.MCP] > 0
    assert breakdown.categories[ContextCategory.SUBAGENT_DEFINITIONS] > 0
    assert breakdown.categories[ContextCategory.CONVERSATION] > 0
    assert breakdown.categories[ContextCategory.SUMMARIZED] > 0
    assert breakdown.total_tokens == sum(breakdown.categories.values())
    assert breakdown.percent_used is not None


def test_render_context_command_matches_format() -> None:
    breakdown = compute_context_breakdown(
        system_prompt="<stable>\nx\n</stable>",
        tools=[],
        messages=[{"role": "user", "content": "hi"}],
        context_window=1000,
    )
    text = render_context_command(breakdown)
    assert text == format_context_breakdown(breakdown)
    assert "Context usage" in text
    assert "system_prompt:" in text
