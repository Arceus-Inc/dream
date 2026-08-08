"""Transcript rewind — Session-owned prompt boundaries."""

from __future__ import annotations

from dream.engine._messages import ConversationMessage, TextBlock
from dream.state.shadow._rewind import rewind_transcript


def _message(role: str, text: str) -> ConversationMessage:
    return ConversationMessage(role=role, content=[TextBlock(text=text)])


def test_rewind_zero_turns_is_noop() -> None:
    messages = [_message("user", "a"), _message("assistant", "b")]
    kept, removed = rewind_transcript(messages, prompt_indices=(0,), turns=0)
    assert kept == messages
    assert removed == 0


def test_rewind_one_turn_drops_last_prompt_cycle() -> None:
    messages = [
        _message("user", "task A"),
        _message("assistant", "done A"),
        _message("user", "task B"),
        _message("assistant", "done B"),
    ]
    kept, removed = rewind_transcript(messages, prompt_indices=(0, 2), turns=1)
    assert kept == messages[:2]
    assert removed == 2


def test_rewind_two_turns_clears_both_cycles() -> None:
    messages = [
        _message("user", "A"),
        _message("assistant", "a"),
        _message("user", "B"),
        _message("assistant", "b"),
    ]
    kept, removed = rewind_transcript(messages, prompt_indices=(0, 2), turns=2)
    assert kept == []
    assert removed == 4


def test_rewind_rejects_unavailable_boundary() -> None:
    messages = [_message("user", "compacted")]
    try:
        rewind_transcript(messages, prompt_indices=(), turns=1)
    except ValueError as exc:
        assert "unavailable" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_rewind_rejects_negative_turns() -> None:
    try:
        rewind_transcript([_message("user", "x")], prompt_indices=(0,), turns=-1)
    except ValueError as exc:
        assert "turns" in str(exc)
    else:
        raise AssertionError("expected ValueError")
