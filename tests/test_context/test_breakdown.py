"""Context pie estimates stay deterministic and category-complete."""

from __future__ import annotations

from dream.context import (
    AdvertisedTool,
    ContextCategory,
    PromptSurfaces,
    compute_context_breakdown,
    format_context_breakdown,
    render_context_command,
)
from dream.engine._messages import ConversationMessage, TextBlock
from dream.prompts import ContextPromptBlock, StablePromptBlock
from dream.services.token_estimation import estimate_tokens
from dream.tools._registry import ToolSource


def _surfaces(*, tools: tuple[AdvertisedTool, ...] = ()) -> PromptSurfaces:
    return PromptSurfaces(
        stable=StablePromptBlock(role=None, include=True),
        context=ContextPromptBlock(
            workspace_governance="# STANDING ORDER\n\nDo the PM thing.",
            skill_catalogue="# Available Skills\n\n- skill: foo",
            memory_catalogue="# Memory catalogue\n\n- memory: bar",
            agents_md="I am the PM.",
            tool_catalogue="",
            subagent_catalogue="",
        ),
        role_instructions=None,
        tools=tools,
    )


def test_breakdown_splits_stable_context_tools_and_conversation() -> None:
    surfaces = _surfaces(
        tools=(
            AdvertisedTool(name="read_file", wire_tokens=12, source=ToolSource.DEFAULT),
            AdvertisedTool(
                name="mcp_github__list", wire_tokens=12, source=ToolSource.MCP
            ),
            AdvertisedTool(
                name="spawn_subagent", wire_tokens=12, source=ToolSource.DEFAULT
            ),
        )
    )
    messages = [
        ConversationMessage(role="user", content=[TextBlock("hello world " * 20)]),
        ConversationMessage(
            role="user",
            content=[
                TextBlock("[Compaction summary — reference only]\nEarlier work done.")
            ],
        ),
    ]

    breakdown = compute_context_breakdown(
        surfaces=surfaces,
        messages=messages,
        context_window=200_000,
    )

    assert breakdown.tokens_for(ContextCategory.SYSTEM_PROMPT) > 0
    assert breakdown.tokens_for(ContextCategory.RULES) > 0
    assert breakdown.tokens_for(ContextCategory.SKILLS) > 0
    assert breakdown.tokens_for(ContextCategory.MEMORY) > 0
    assert breakdown.tokens_for(ContextCategory.TOOL_DEFINITIONS) > 0
    assert breakdown.tokens_for(ContextCategory.MCP) > 0
    assert breakdown.tokens_for(ContextCategory.SUBAGENT_DEFINITIONS) > 0
    assert breakdown.tokens_for(ContextCategory.CONVERSATION) > 0
    assert breakdown.tokens_for(ContextCategory.SUMMARIZED) > 0
    assert breakdown.total_tokens == sum(
        breakdown.tokens_for(cat) for cat in ContextCategory
    )
    assert breakdown.percent_used is not None


def test_skills_section_stops_before_memory() -> None:
    surfaces = PromptSurfaces(
        stable=StablePromptBlock(role=None, include=True),
        context=ContextPromptBlock(
            workspace_governance="<stable>\nSTANDING\n</stable>\n\n<context>",
            skill_catalogue="# Available Skills\n\n- skill: foo",
            memory_catalogue="# Memory catalogue\n\n- memory: bar",
            agents_md="",
            tool_catalogue="",
            subagent_catalogue="",
        ),
        role_instructions=None,
        tools=(),
    )
    breakdown = compute_context_breakdown(surfaces=surfaces)
    # Skills must not absorb the memory section (double-count / misattribute).
    skills = breakdown.tokens_for(ContextCategory.SKILLS)
    memory = breakdown.tokens_for(ContextCategory.MEMORY)
    assert skills > 0
    assert memory > 0
    # Memory tokens must not also appear in skills: skills text is shorter than
    # skills+memory concatenated.
    skills_only = estimate_tokens("# Available Skills\n\n- skill: foo")
    skills_plus_memory = estimate_tokens(
        "# Available Skills\n\n- skill: foo\n\n# Memory catalogue\n\n- memory: bar"
    )
    assert skills <= skills_only + 2
    assert skills < skills_plus_memory


def test_render_context_command_matches_format() -> None:
    surfaces = PromptSurfaces(
        stable=StablePromptBlock(role=None, include=True),
        context=ContextPromptBlock(
            workspace_governance="<stable>\nx\n</stable>",
            skill_catalogue="",
            memory_catalogue="",
            agents_md="",
            tool_catalogue="",
            subagent_catalogue="",
        ),
        role_instructions=None,
        tools=(),
    )
    breakdown = compute_context_breakdown(
        surfaces=surfaces,
        messages=[ConversationMessage(role="user", content=[TextBlock("hi")])],
        context_window=1000,
    )
    text = render_context_command(breakdown)
    assert text == format_context_breakdown(breakdown)
    assert "Context usage" in text
    assert "system_prompt:" in text