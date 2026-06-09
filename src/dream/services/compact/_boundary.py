"""Atom-safe compaction boundary (Spec 00 #1).

A preserve boundary must never split a tool_use/result pair — the most
dangerous site for the invariant. These pure helpers find a safe split point
and sanitise the newer segment so no orphan ``ToolUseBlock`` survives.
"""

from __future__ import annotations

from dream.engine._messages import (
    ConversationMessage,
    ToolResultBlock,
    ToolUseBlock,
    sanitize_conversation_messages,
)


def boundary_crosses_tool_pair(
    previous: ConversationMessage, current: ConversationMessage
) -> bool:
    """True when a preserve boundary would split a tool_use/result pair."""
    if previous.role != "assistant" or current.role != "user":
        return False
    pending_tool_ids = {
        block.id for block in previous.content if isinstance(block, ToolUseBlock)
    }
    if not pending_tool_ids:
        return False
    result_ids = {
        block.tool_use_id
        for block in current.content
        if isinstance(block, ToolResultBlock)
    }
    return bool(pending_tool_ids & result_ids)


def split_preserving_tool_pairs(
    messages: list[ConversationMessage], *, preserve_recent: int
) -> tuple[list[ConversationMessage], list[ConversationMessage]]:
    """Split older/newer without cutting through a tool_use/result pair.

    The newer segment is also sanitised so a trailing orphan ``ToolUseBlock``
    never survives the boundary.
    """
    preserve_recent = max(0, preserve_recent)
    if len(messages) <= preserve_recent:
        return [], sanitize_conversation_messages(list(messages))

    split_index = max(0, len(messages) - preserve_recent)
    # split_index == len(messages) (preserve_recent == 0) means the whole
    # transcript is "older" and nothing is preserved — there is no boundary to
    # check, and messages[split_index] would be out of range.
    while (
        0 < split_index < len(messages)
        and boundary_crosses_tool_pair(
            messages[split_index - 1], messages[split_index]
        )
    ):
        split_index -= 1

    older = list(messages[:split_index])
    newer = sanitize_conversation_messages(list(messages[split_index:]))
    return older, newer


__all__ = [
    "boundary_crosses_tool_pair",
    "split_preserving_tool_pairs",
]
