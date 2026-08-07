"""Transcript rewind — Hermes /rollback conversation half (pure)."""

from __future__ import annotations

from dream.engine._messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from dream.state.shadow._rewind import (
    is_user_prompt_message,
    rewind_transcript,
    user_prompt_indices,
)


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _assistant(text: str) -> ConversationMessage:
    return ConversationMessage(role="assistant", content=[TextBlock(text=text)])


def _assistant_tools(tool_id: str, name: str) -> ConversationMessage:
    return ConversationMessage(
        role="assistant",
        content=[ToolUseBlock(id=tool_id, name=name, input={"path": "a.py"})],
    )


def _tool_results(tool_id: str) -> ConversationMessage:
    return ConversationMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id=tool_id, content="ok")],
    )


def test_is_user_prompt_ignores_tool_result_only_messages() -> None:
    assert is_user_prompt_message(_user("do the thing")) is True
    assert is_user_prompt_message(_tool_results("t1")) is False
    assert is_user_prompt_message(_assistant("hi")) is False


def test_user_prompt_indices_finds_prompt_boundaries() -> None:
    messages = [
        _user("task A"),
        _assistant_tools("t1", "write_file"),
        _tool_results("t1"),
        _assistant("done A"),
        _user("task B"),
        _assistant("done B"),
    ]
    assert user_prompt_indices(messages) == (0, 4)


def test_rewind_zero_turns_is_noop() -> None:
    messages = [_user("a"), _assistant("b")]
    kept, removed = rewind_transcript(messages, turns=0)
    assert kept == messages
    assert removed == 0


def test_rewind_one_turn_drops_last_prompt_cycle() -> None:
    messages = [
        _user("task A"),
        _assistant("done A"),
        _user("task B"),
        _assistant_tools("t1", "edit_file"),
        _tool_results("t1"),
        _assistant("done B"),
    ]
    kept, removed = rewind_transcript(messages, turns=1)
    assert kept == messages[:2]
    assert removed == 4


def test_rewind_two_turns_clears_both_cycles() -> None:
    messages = [
        _user("A"),
        _assistant("a"),
        _user("B"),
        _assistant("b"),
    ]
    kept, removed = rewind_transcript(messages, turns=2)
    assert kept == []
    assert removed == 4


def test_rewind_more_than_available_clears_all() -> None:
    messages = [_user("only"), _assistant("ok")]
    kept, removed = rewind_transcript(messages, turns=9)
    assert kept == []
    assert removed == 2


def test_rewind_rejects_negative_turns() -> None:
    try:
        rewind_transcript([_user("x")], turns=-1)
    except ValueError as exc:
        assert "turns" in str(exc)
    else:
        raise AssertionError("expected ValueError")
