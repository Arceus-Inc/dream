"""Transcript rewind helpers — Hermes ``/rollback`` conversation half.

Filesystem restore lives on :class:`ShadowCheckpointManager`. These pure
functions truncate the in-memory conversation so FS and chat stay aligned
after an operator rewind.
"""

from __future__ import annotations

from collections.abc import Sequence

from dream.engine._messages import ConversationMessage, TextBlock, ToolResultBlock


def is_user_prompt_message(message: ConversationMessage) -> bool:
    """True when ``message`` starts a human/agent turn (not a tool-result batch).

    Tool-result user messages only carry :class:`ToolResultBlock`s. A real
    prompt has at least one :class:`TextBlock` (and may also mix other blocks).
    """
    if message.role != "user":
        return False
    has_text = False
    for block in message.content:
        if isinstance(block, ToolResultBlock):
            continue
        if isinstance(block, TextBlock):
            if block.text.strip():
                has_text = True
            continue
        # Image / other non-tool content still counts as a prompt.
        return True
    return has_text


def user_prompt_indices(messages: Sequence[ConversationMessage]) -> tuple[int, ...]:
    """Indices of user-prompt messages in order (oldest first)."""
    return tuple(i for i, msg in enumerate(messages) if is_user_prompt_message(msg))


def rewind_transcript(
    messages: Sequence[ConversationMessage],
    *,
    turns: int,
) -> tuple[list[ConversationMessage], int]:
    """Drop the last ``turns`` user-prompt cycles from ``messages``.

    A cycle starts at a user-prompt message and includes everything until
    (but not including) the next user-prompt, or EOF. ``turns=0`` is a no-op.

    Returns ``(kept_messages, removed_count)``.
    """
    if turns < 0:
        raise ValueError(f"turns must be >= 0; got {turns}")
    if turns == 0:
        return list(messages), 0

    indices = user_prompt_indices(messages)
    if not indices:
        return list(messages), 0

    drop_from = indices[-turns] if turns <= len(indices) else indices[0]
    kept = list(messages[:drop_from])
    removed = len(messages) - len(kept)
    return kept, removed


__all__ = [
    "is_user_prompt_message",
    "rewind_transcript",
    "user_prompt_indices",
]
