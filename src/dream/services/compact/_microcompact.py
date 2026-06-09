"""Microcompact tier — drop the content of old, droppable tool results.

The cheap deterministic tier of two-tier compaction (Spec 04 #2): walk the
transcript, find settled tool results that are safe to drop, and replace their
content with a sentinel. Pure transform — *returns new messages, never mutates
input*.
"""

from __future__ import annotations

from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.services.token_estimation import estimate_tokens
from dream.services.tool_outputs import is_microcompactable_tool_result

# Canonical local tools whose old results are always safe to drop. MCP results
# and large non-MCP results also become eligible via the
# ``is_microcompactable_tool_result`` predicate.
COMPACTABLE_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "bash",
        "grep",
        "glob",
        "web_search",
        "web_fetch",
        "edit_file",
        "write_file",
    }
)

TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"
DEFAULT_KEEP_RECENT = 5

# A canonical-tool result is only worth clearing when its content is larger
# than the sentinel that replaces it; otherwise microcompaction would *grow*
# the transcript while reporting savings (negative compaction).
_MC_REPLACEMENT_SIZE = len(TIME_BASED_MC_CLEARED_MESSAGE)


def collect_compactable_tool_ids(messages: list[ConversationMessage]) -> list[str]:
    """Walk messages and collect tool_use IDs whose results are compactable.

    Order is chronological (oldest first) so callers can keep the most recent
    ``keep_recent`` and clear the rest.
    """
    ordered_ids: list[str] = []
    tool_names: dict[str, str] = {}
    result_content: dict[str, str] = {}
    for msg in messages:
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                ordered_ids.append(block.id)
                tool_names[block.id] = block.name
            elif isinstance(block, ToolResultBlock):
                result_content[block.tool_use_id] = block.content
    compactable: list[str] = []
    for tool_id in ordered_ids:
        name = tool_names.get(tool_id, "")
        content = result_content.get(tool_id, "")
        if name in COMPACTABLE_TOOLS:
            # Only clear when the payload is larger than its replacement
            # sentinel — otherwise we'd grow the transcript (negative
            # compaction) while reporting savings.
            if len(content) > _MC_REPLACEMENT_SIZE:
                compactable.append(tool_id)
        elif is_microcompactable_tool_result(name, content):
            compactable.append(tool_id)
    return compactable


def microcompact_messages(
    messages: list[ConversationMessage],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> tuple[list[ConversationMessage], int]:
    """Replace old compactable tool-result content with a sentinel.

    Returns a new list (input is not mutated) plus the estimated tokens
    reclaimed. ``keep_recent`` is clamped to ``max(1, keep_recent)`` so we
    never erase every tool result — at least one anchor stays.
    """
    keep_recent = max(1, keep_recent)
    all_ids = collect_compactable_tool_ids(messages)

    if len(all_ids) <= keep_recent:
        return list(messages), 0

    keep_set = set(all_ids[-keep_recent:])
    clear_set = set(all_ids) - keep_set

    tokens_saved = 0
    out: list[ConversationMessage] = []
    for msg in messages:
        if msg.role != "user":
            out.append(msg)
            continue
        new_content: list[ContentBlock] = []
        changed = False
        for block in msg.content:
            if (
                isinstance(block, ToolResultBlock)
                and block.tool_use_id in clear_set
                and block.content != TIME_BASED_MC_CLEARED_MESSAGE
            ):
                tokens_saved += estimate_tokens(block.content)
                new_content.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=TIME_BASED_MC_CLEARED_MESSAGE,
                        is_error=block.is_error,
                    )
                )
                changed = True
            else:
                new_content.append(block)
        out.append(
            ConversationMessage(role=msg.role, content=new_content) if changed else msg
        )

    return out, tokens_saved


__all__ = [
    "COMPACTABLE_TOOLS",
    "DEFAULT_KEEP_RECENT",
    "TIME_BASED_MC_CLEARED_MESSAGE",
    "collect_compactable_tool_ids",
    "microcompact_messages",
]
