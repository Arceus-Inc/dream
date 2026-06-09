"""Reactive prompt-too-long (PTL) shrink primitives (Spec 04 #3).

Deterministic shrinks the reactive path uses before paying for full
compaction: collapse oversized text/tool-result blocks in the older segment,
then fall back to dropping the oldest prompt rounds.
"""

from __future__ import annotations

from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    sanitize_conversation_messages,
)
from dream.services.compact._boundary import split_preserving_tool_pairs
from dream.services.token_estimation import estimate_conversation_tokens

# Char limits for try_context_collapse. Picked to match openharness so the
# behaviour is identical when both harnesses see the same transcript.
CONTEXT_COLLAPSE_TEXT_CHAR_LIMIT = 2_400
CONTEXT_COLLAPSE_HEAD_CHARS = 900
CONTEXT_COLLAPSE_TAIL_CHARS = 500

PTL_RETRY_MARKER = "[earlier conversation truncated for compaction retry]"


def _collapse_text(text: str) -> str:
    if len(text) <= CONTEXT_COLLAPSE_TEXT_CHAR_LIMIT:
        return text
    omitted = len(text) - CONTEXT_COLLAPSE_HEAD_CHARS - CONTEXT_COLLAPSE_TAIL_CHARS
    head = text[:CONTEXT_COLLAPSE_HEAD_CHARS].rstrip()
    tail = text[-CONTEXT_COLLAPSE_TAIL_CHARS:].lstrip()
    return f"{head}\n...[collapsed {omitted} chars]...\n{tail}"


def try_context_collapse(
    messages: list[ConversationMessage], *, preserve_recent: int
) -> list[ConversationMessage] | None:
    """Deterministically shrink oversized text blocks before paying for full compact.

    Returns ``None`` when there is nothing useful to collapse (so the caller
    can fall through to full compaction); otherwise returns a new message
    list whose token estimate is strictly lower.
    """
    if len(messages) <= preserve_recent + 2:
        return None

    older, newer = split_preserving_tool_pairs(messages, preserve_recent=preserve_recent)
    changed = False
    collapsed_older: list[ConversationMessage] = []
    for message in older:
        new_blocks: list[ContentBlock] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                collapsed = _collapse_text(block.text)
                if collapsed != block.text:
                    changed = True
                new_blocks.append(TextBlock(text=collapsed))
            elif isinstance(block, ToolResultBlock):
                collapsed = _collapse_text(block.content)
                if collapsed != block.content:
                    changed = True
                new_blocks.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=collapsed,
                        is_error=block.is_error,
                    )
                )
            else:
                new_blocks.append(block)
        collapsed_older.append(ConversationMessage(role=message.role, content=new_blocks))

    if not changed:
        return None

    result = [*collapsed_older, *newer]
    if estimate_conversation_tokens(result) >= estimate_conversation_tokens(messages):
        return None
    return result


def _group_messages_by_prompt_round(
    messages: list[ConversationMessage],
) -> list[list[ConversationMessage]]:
    groups: list[list[ConversationMessage]] = []
    current: list[ConversationMessage] = []
    for message in messages:
        starts_new_round = (
            message.role == "user"
            and not any(
                isinstance(block, ToolResultBlock) for block in message.content
            )
            and bool(message.text.strip())
        )
        if starts_new_round and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)
    return groups


def truncate_head_for_ptl_retry(
    messages: list[ConversationMessage],
) -> list[ConversationMessage] | None:
    """Drop the oldest prompt rounds when reactive compaction needs aggressive room.

    Returns ``None`` when there is only a single round (nothing safe to drop).
    Otherwise drops ``max(1, n_rounds // 5)`` of the oldest rounds. If the
    surviving head starts with an assistant message (orphaned), prepend a
    user marker so the transcript remains provider-valid.
    """
    groups = _group_messages_by_prompt_round(messages)
    if len(groups) < 2:
        return None

    drop_count = max(1, len(groups) // 5)
    drop_count = min(drop_count, len(groups) - 1)
    retained = [message for group in groups[drop_count:] for message in group]
    if not retained:
        return None
    if retained[0].role == "assistant":
        marker = ConversationMessage(
            role="user", content=[TextBlock(text=PTL_RETRY_MARKER)]
        )
        retained = [marker, *retained]
    # Re-sanitize: dropping head rounds can leave the retained tail ending on
    # an orphan assistant ToolUseBlock, which the next provider call rejects.
    return sanitize_conversation_messages(retained)


__all__ = [
    "CONTEXT_COLLAPSE_HEAD_CHARS",
    "CONTEXT_COLLAPSE_TAIL_CHARS",
    "CONTEXT_COLLAPSE_TEXT_CHAR_LIMIT",
    "PTL_RETRY_MARKER",
    "truncate_head_for_ptl_retry",
    "try_context_collapse",
]
