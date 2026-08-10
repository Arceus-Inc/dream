"""Cursor / Hermes-style context-window breakdown for a live session."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_STABLE_RE = re.compile(r"<stable>(.*?)</stable>", re.DOTALL)
_CONTEXT_RE = re.compile(r"<context>(.*?)</context>", re.DOTALL)
_ROLE_RE = re.compile(r"<role>(.*?)</role>", re.DOTALL)
_AGENTS_RE = re.compile(r"# AGENTS\.md\n\n(.*?)(?=\n\n# |\n\n<|$)", re.DOTALL)
_SKILLS_MARKERS = ("Available Skills", "skill_catalogue", "<available_skills>")
_MEMORY_MARKERS = ("Memory catalogue", "memory_catalogue", "# Memory")
_SUBAGENT_TOOLS = frozenset({"spawn_subagent", "delegate_task", "delegation_get", "delegation_stop"})


class ContextCategory(StrEnum):
    SYSTEM_PROMPT = "system_prompt"
    RULES = "rules"
    SKILLS = "skills"
    TOOL_DEFINITIONS = "tool_definitions"
    MCP = "mcp"
    SUBAGENT_DEFINITIONS = "subagent_definitions"
    MEMORY = "memory"
    SUMMARIZED = "summarized_conversation"
    CONVERSATION = "conversation"


@dataclass(frozen=True)
class ContextBreakdown:
    """Estimated token shares for the next provider request."""

    categories: Mapping[ContextCategory, int]
    total_tokens: int
    context_window: int | None = None

    @property
    def percent_used(self) -> float | None:
        if not self.context_window:
            return None
        return 100.0 * self.total_tokens / self.context_window


def estimate_tokens(text: str) -> int:
    """Rough char/4 heuristic aligned with Hermes context breakdown."""
    if not text:
        return 0
    return (len(text) + 3) // 4


def compute_context_breakdown(
    *,
    system_prompt: str,
    tools: Sequence[Mapping[str, Any]] | None = None,
    messages: Sequence[Mapping[str, Any] | Any] | None = None,
    context_window: int | None = None,
) -> ContextBreakdown:
    """Split a request into Cursor-pie categories."""
    stable = _match_group(_STABLE_RE, system_prompt)
    context = _match_group(_CONTEXT_RE, system_prompt)
    role = _match_group(_ROLE_RE, system_prompt)
    if not stable and not context and system_prompt.strip():
        stable = system_prompt.strip()

    agents = _match_group(_AGENTS_RE, context)
    skills = _section_with_markers(context, _SKILLS_MARKERS)
    memory = _section_with_markers(context, _MEMORY_MARKERS)
    rules = _remainder(context, agents, skills, memory)

    tool_tokens, mcp_tokens, subagent_tokens = _split_tool_tokens(tools or ())
    conversation_tokens, summarized_tokens = _message_tokens(messages or ())

    categories = {
        ContextCategory.SYSTEM_PROMPT: estimate_tokens(stable) + estimate_tokens(role),
        ContextCategory.RULES: estimate_tokens(agents) + estimate_tokens(rules),
        ContextCategory.SKILLS: estimate_tokens(skills),
        ContextCategory.TOOL_DEFINITIONS: tool_tokens,
        ContextCategory.MCP: mcp_tokens,
        ContextCategory.SUBAGENT_DEFINITIONS: subagent_tokens,
        ContextCategory.MEMORY: estimate_tokens(memory),
        ContextCategory.SUMMARIZED: summarized_tokens,
        ContextCategory.CONVERSATION: conversation_tokens,
    }
    total = sum(categories.values())
    return ContextBreakdown(
        categories=categories,
        total_tokens=total,
        context_window=context_window,
    )


def format_context_breakdown(breakdown: ContextBreakdown) -> str:
    """Human-readable multi-line summary for operators."""
    lines = ["Context usage"]
    if breakdown.context_window:
        pct = breakdown.percent_used or 0.0
        lines.append(
            f"{breakdown.total_tokens:,} / {breakdown.context_window:,} tokens ({pct:.0f}%)"
        )
    else:
        lines.append(f"{breakdown.total_tokens:,} tokens (window unknown)")
    for category in ContextCategory:
        tokens = breakdown.categories.get(category, 0)
        if tokens == 0:
            continue
        share = (100.0 * tokens / breakdown.total_tokens) if breakdown.total_tokens else 0.0
        lines.append(f"  {category.value}: {tokens:,} ({share:.0f}%)")
    return "\n".join(lines)


def render_context_command(breakdown: ContextBreakdown) -> str:
    """Slash-command style payload for a future REPL ``/context`` handler."""
    return format_context_breakdown(breakdown)


def _match_group(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def _section_with_markers(text: str, markers: Sequence[str]) -> str:
    if not text:
        return ""
    lower = text.lower()
    for marker in markers:
        idx = lower.find(marker.lower())
        if idx >= 0:
            return text[idx:].strip()
    return ""


def _remainder(text: str, *parts: str) -> str:
    out = text or ""
    for part in parts:
        if part:
            out = out.replace(part, "")
    return out.strip()


def _tool_name(tool: Mapping[str, Any]) -> str:
    fn = tool.get("function")
    if isinstance(fn, Mapping):
        return str(fn.get("name") or "")
    return str(tool.get("name") or "")


def _split_tool_tokens(tools: Sequence[Mapping[str, Any]]) -> tuple[int, int, int]:
    builtin = 0
    mcp = 0
    subagent = 0
    for tool in tools:
        tokens = estimate_tokens(json.dumps(tool, ensure_ascii=False, default=str))
        name = _tool_name(tool)
        if name.startswith("mcp_") or name.startswith("mcp__"):
            mcp += tokens
        elif name in _SUBAGENT_TOOLS:
            subagent += tokens
        else:
            builtin += tokens
    return builtin, mcp, subagent


def _message_tokens(messages: Sequence[Any]) -> tuple[int, int]:
    conversation = 0
    summarized = 0
    for message in messages:
        text = _message_text(message)
        tokens = estimate_tokens(text)
        if "Compaction summary" in text or "[Compaction summary" in text:
            summarized += tokens
        else:
            conversation += tokens
    return conversation, summarized


def _message_text(message: Any) -> str:
    if isinstance(message, Mapping):
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False, default=str)
    text = getattr(message, "text", None)
    if isinstance(text, str) and text:
        return text
    content = getattr(message, "content", None)
    if content is None:
        return str(message)
    return json.dumps(content, ensure_ascii=False, default=str)


__all__ = [
    "ContextBreakdown",
    "ContextCategory",
    "compute_context_breakdown",
    "estimate_tokens",
    "format_context_breakdown",
    "render_context_command",
]
