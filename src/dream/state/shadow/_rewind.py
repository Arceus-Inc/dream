"""Transcript rewind helpers — Hermes ``/rollback`` conversation half.

Filesystem restore lives on :class:`ShadowCheckpointManager`. These pure
functions truncate the in-memory conversation so FS and chat stay aligned
after an operator rewind.
"""

from __future__ import annotations

from collections.abc import Sequence

from dream.engine._messages import ConversationMessage


def rewind_transcript(
    messages: Sequence[ConversationMessage],
    *,
    prompt_indices: Sequence[int],
    turns: int,
) -> tuple[list[ConversationMessage], int]:
    """Drop the last ``turns`` prompt cycles using Session-owned boundaries.

    Returns ``(kept_messages, removed_count)``.
    """
    if turns < 0:
        raise ValueError(f"turns must be >= 0; got {turns}")
    if turns == 0:
        return list(messages), 0

    if turns > len(prompt_indices):
        raise ValueError("requested rewind boundary is unavailable")

    drop_from = prompt_indices[-turns]
    kept = list(messages[:drop_from])
    removed = len(messages) - len(kept)
    return kept, removed


__all__ = [
    "rewind_transcript",
]
