"""Context-window observability: break a session's next request into categories.

The provider request for a session composes a stable system prompt, a context
block (governance, catalogues), role instructions, a ``tools`` wire, and the
transcript. This module reports how many tokens each surface is estimated to
cost so operators can see exactly where a session's context window is going
(REPL ``/context``).

The estimates are deliberately cheap and deterministic: plain-text stretches
use :func:`dream.services.token_estimation.estimate_tokens`, and the
transcript delegates to
:func:`dream.services.token_estimation.estimate_conversation_tokens`. Nothing
here reads the provider back; the numbers are the same 4-characters-per-token
walk the compactor already trusts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from dream.engine._messages import ConversationMessage, TextBlock
from dream.prompts.system_prompt import ContextPromptBlock, StablePromptBlock
from dream.services.token_estimation import (
    estimate_conversation_tokens,
    estimate_tokens,
    resolve_context_window,
)
from dream.tools._registry import ToolSource

__all__ = [
    "AdvertisedTool",
    "ContextBreakdown",
    "ContextCategory",
    "PromptSurfaces",
    "compute_context_breakdown",
    "estimate_tokens",
    "format_context_breakdown",
    "render_context_command",
    "resolve_context_window",
]

# Subagent-ish tools are DEFAULT-builtin names (no ``ToolSource`` marker), so a
# name set is the only way to split them out of the builtin bucket.
_SUBAGENT_TOOLS = frozenset({"spawn_subagent", "delegate_task", "delegation_get", "delegation_stop"})

# The ``[Compaction summary`` marker is exactly what the summariser writes as
# the first line of a compacted transcript message (see
# ``dream.services.compact._summariser``).
_COMPACTION_MARKER = "[Compaction summary"


class ContextCategory(StrEnum):
    """The nine surfaces of one provider request, listed in order of renders."""

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
class AdvertisedTool:
    """One tool advertised to the model: its serialized size and provenance.

    ``wire_tokens`` is the estimated token cost of this tool's OpenAI ``tools``
    wire entry. ``source`` carries :class:`ToolSource` provenance so MCP tools
    can be separated from builtins without name sniffing.
    """

    name: str
    wire_tokens: int
    source: ToolSource


@dataclass(frozen=True)
class PromptSurfaces:
    """The typed surfaces a session sent to the provider, for ``/context``.

    Held on the ``QueryEngine`` in place of the raw assembled prompt string:
    the breakdown reads governance and catalogues straight off the typed
    prompt blocks rather than re-parsing rendered text.
    """

    stable: StablePromptBlock | None = None
    context: ContextPromptBlock | None = None
    role_instructions: str | None = None
    tools: tuple[AdvertisedTool, ...] = ()


@dataclass(frozen=True)
class ContextBreakdown:
    """Estimated token shares per category for the next provider request."""

    system_prompt_tokens: int
    rules_tokens: int
    skills_tokens: int
    tool_definitions_tokens: int
    mcp_tokens: int
    subagent_definitions_tokens: int
    memory_tokens: int
    summarized_tokens: int
    conversation_tokens: int
    context_window: int | None = None

    @property
    def total_tokens(self) -> int:
        """Estimated tokens across every category."""
        return sum(
            (
                self.system_prompt_tokens,
                self.rules_tokens,
                self.skills_tokens,
                self.tool_definitions_tokens,
                self.mcp_tokens,
                self.subagent_definitions_tokens,
                self.memory_tokens,
                self.summarized_tokens,
                self.conversation_tokens,
            )
        )

    @property
    def percent_used(self) -> float | None:
        """Percent of the context window this request would consume."""
        if self.context_window is None:
            return None
        return 100.0 * self.total_tokens / self.context_window

    def tokens_for(self, category: ContextCategory) -> int:
        """Estimated tokens attributable to ``category``."""
        match category:
            case ContextCategory.SYSTEM_PROMPT:
                return self.system_prompt_tokens
            case ContextCategory.RULES:
                return self.rules_tokens
            case ContextCategory.SKILLS:
                return self.skills_tokens
            case ContextCategory.TOOL_DEFINITIONS:
                return self.tool_definitions_tokens
            case ContextCategory.MCP:
                return self.mcp_tokens
            case ContextCategory.SUBAGENT_DEFINITIONS:
                return self.subagent_definitions_tokens
            case ContextCategory.MEMORY:
                return self.memory_tokens
            case ContextCategory.SUMMARIZED:
                return self.summarized_tokens
            case ContextCategory.CONVERSATION:
                return self.conversation_tokens


@dataclass(frozen=True)
class _ToolSplit:
    """Token totals split into the three tool buckets."""

    builtin: int
    mcp: int
    subagent: int


def compute_context_breakdown(
    *,
    surfaces: PromptSurfaces,
    messages: Sequence[ConversationMessage] = (),
    context_window: int | None = None,
) -> ContextBreakdown:
    """Split a next request into per-category token estimates.

    Prompts are read from the typed ``StablePromptBlock`` /
    ``ContextPromptBlock`` on ``surfaces``, tool tokens are summed from
    ``surfaces.tools`` (grouped by provenance), and the transcript is walked
    with the compactor's own token estimator, splitting compaction summaries
    into their own bucket.
    """
    stable = surfaces.stable
    context = surfaces.context

    system_prompt_tokens = _system_prompt_tokens(stable, surfaces.role_instructions)
    context_tokens = _context_tokens(context)
    tools = _tool_split(surfaces.tools)
    summarized_tokens, conversation_tokens = _transcript_split(messages)

    return ContextBreakdown(
        system_prompt_tokens=system_prompt_tokens,
        rules_tokens=context_tokens.rules,
        skills_tokens=context_tokens.skills,
        tool_definitions_tokens=context_tokens.tool_catalogue + tools.builtin,
        mcp_tokens=tools.mcp,
        subagent_definitions_tokens=context_tokens.subagent_catalogue + tools.subagent,
        memory_tokens=context_tokens.memory,
        summarized_tokens=summarized_tokens,
        conversation_tokens=conversation_tokens,
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
        tokens = breakdown.tokens_for(category)
        if tokens == 0:
            continue
        share = (100.0 * tokens / breakdown.total_tokens) if breakdown.total_tokens else 0.0
        lines.append(f"  {category.value}: {tokens:,} ({share:.0f}%)")
    return "\n".join(lines)


def render_context_command(breakdown: ContextBreakdown) -> str:
    """Slash-command payload for a REPL ``/context`` handler.

    Kept as a distinct entry point so a future command can render
    differently (e.g. extra tool detail) without callers changing.
    """
    return format_context_breakdown(breakdown)


@dataclass(frozen=True)
class _ContextSplit:
    """Token totals read off a ``ContextPromptBlock`` text surfaces."""

    rules: int
    skills: int
    tool_catalogue: int
    subagent_catalogue: int
    memory: int


def _system_prompt_tokens(
    stable: StablePromptBlock | None,
    role_instructions: str | None,
) -> int:
    return estimate_tokens(stable.render() if stable else "") + estimate_tokens(
        role_instructions or ""
    )


def _context_tokens(context: ContextPromptBlock | None) -> _ContextSplit:
    if context is None:
        return _ContextSplit(rules=0, skills=0, tool_catalogue=0, subagent_catalogue=0, memory=0)
    return _ContextSplit(
        rules=estimate_tokens(context.workspace_governance) + estimate_tokens(context.agents_md),
        skills=estimate_tokens(context.skill_catalogue),
        tool_catalogue=estimate_tokens(context.tool_catalogue),
        subagent_catalogue=estimate_tokens(context.subagent_catalogue),
        memory=estimate_tokens(context.memory_catalogue),
    )


def _tool_split(tools: Sequence[AdvertisedTool]) -> _ToolSplit:
    builtin = 0
    mcp = 0
    subagent = 0
    for tool in tools:
        if tool.source is ToolSource.MCP:
            mcp += tool.wire_tokens
        elif tool.name in _SUBAGENT_TOOLS:
            subagent += tool.wire_tokens
        else:
            builtin += tool.wire_tokens
    return _ToolSplit(builtin=builtin, mcp=mcp, subagent=subagent)


def _transcript_split(messages: Sequence[ConversationMessage]) -> tuple[int, int]:
    summarized = 0
    conversation = 0
    for message in messages:
        tokens = estimate_conversation_tokens((message,))
        if _is_summarized(message):
            summarized += tokens
        else:
            conversation += tokens
    return summarized, conversation


def _is_summarized(message: ConversationMessage) -> bool:
    return any(
        isinstance(block, TextBlock) and block.text.startswith(_COMPACTION_MARKER)
        for block in message.content
    )