"""Spec 04 stage 4b — atom-safe boundary splitting.

The single most dangerous site for the tool-call atom (Spec 00 #1) is the
compaction boundary: cutting between an assistant ``tool_use`` and the user
``tool_result`` that answers it produces a transcript no provider will
accept. ``boundary_crosses_tool_pair`` is the guard; ``split_preserving_tool_pairs``
the splitter that walks the boundary backwards until safe.
"""

from __future__ import annotations

from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    sanitize_conversation_messages,
)
from dream.services.compact import (
    boundary_crosses_tool_pair,
    split_preserving_tool_pairs,
)


def _user_text(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _assistant_text(text: str) -> ConversationMessage:
    return ConversationMessage(role="assistant", content=[TextBlock(text=text)])


def _assistant_tool_use(tid: str, name: str = "read_file") -> ConversationMessage:
    return ConversationMessage(
        role="assistant",
        content=[ToolUseBlock(id=tid, name=name, input={})],
    )


def _user_tool_result(tid: str, content: str = "ok") -> ConversationMessage:
    return ConversationMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id=tid, content=content)],
    )


# --- boundary_crosses_tool_pair ---------------------------------------------


def test_crosses_when_assistant_tool_use_meets_matching_user_result() -> None:
    prev = _assistant_tool_use("t1")
    curr = _user_tool_result("t1")
    assert boundary_crosses_tool_pair(prev, curr) is True


def test_does_not_cross_when_prev_is_text_only_assistant() -> None:
    assert boundary_crosses_tool_pair(_assistant_text("done"), _user_text("ok")) is False


def test_does_not_cross_when_prev_is_user() -> None:
    """A user message can't open a tool atom."""
    assert boundary_crosses_tool_pair(_user_text("hi"), _user_text("hi")) is False


def test_does_not_cross_when_tool_ids_differ() -> None:
    prev = _assistant_tool_use("t1")
    curr = _user_tool_result("t2")
    assert boundary_crosses_tool_pair(prev, curr) is False


def test_does_not_cross_when_current_has_no_tool_results() -> None:
    prev = _assistant_tool_use("t1")
    curr = _user_text("plain text follow-up")
    assert boundary_crosses_tool_pair(prev, curr) is False


# --- split_preserving_tool_pairs --------------------------------------------


def test_split_short_list_returns_empty_older() -> None:
    messages = [_user_text("hello"), _assistant_text("hi back")]
    older, newer = split_preserving_tool_pairs(messages, preserve_recent=5)
    assert older == []
    assert len(newer) == 2


def test_split_clean_boundary_moves_oldest_into_older() -> None:
    messages = [
        _user_text("turn 1"),
        _assistant_text("reply 1"),
        _user_text("turn 2"),
        _assistant_text("reply 2"),
        _user_text("turn 3"),
        _assistant_text("reply 3"),
    ]
    older, newer = split_preserving_tool_pairs(messages, preserve_recent=2)
    assert len(older) + len(newer) == len(messages)
    assert older[0] is messages[0]
    assert newer[-1] is messages[-1]


def test_split_walks_back_when_boundary_would_split_a_tool_pair() -> None:
    """Initial split index falls between use+result; splitter must move backwards."""
    messages = [
        _user_text("setup"),
        _assistant_tool_use("t1"),
        _user_tool_result("t1"),
        _assistant_text("done"),
    ]
    # preserve_recent=2 puts boundary between [use] | [result, done] — splits the pair
    older, newer = split_preserving_tool_pairs(messages, preserve_recent=2)
    # Splitter moves boundary back so the use+result pair lands together in newer
    use_in_older = any(
        isinstance(b, ToolUseBlock)
        for msg in older
        for b in msg.content
    )
    result_in_newer = any(
        isinstance(b, ToolResultBlock)
        for msg in newer
        for b in msg.content
    )
    assert not use_in_older
    assert result_in_newer


def test_split_can_walk_back_to_zero_to_preserve_a_pair() -> None:
    """Pathological case: every split would orphan a tool_use."""
    messages = [
        _assistant_tool_use("t1"),
        _user_tool_result("t1"),
    ]
    older, newer = split_preserving_tool_pairs(messages, preserve_recent=1)
    assert older == []
    assert len(newer) == 2


def test_split_sanitises_trailing_orphan_tool_use_in_newer_segment() -> None:
    """``sanitize_conversation_messages`` is applied to the newer segment so a
    trailing assistant ``tool_use`` without a matching result never survives."""
    messages = [
        _user_text("setup"),
        _assistant_text("reply"),
        _assistant_tool_use("t1"),  # orphan — no following tool_result
    ]
    _, newer = split_preserving_tool_pairs(messages, preserve_recent=2)
    has_dangling_use = any(
        isinstance(b, ToolUseBlock)
        for msg in newer
        for b in msg.content
    )
    assert not has_dangling_use


def test_split_round_trip_is_provider_safe() -> None:
    """The recombined older+newer transcript must pass sanitize unchanged."""
    messages = [
        _user_text("turn 1"),
        _assistant_tool_use("t1"),
        _user_tool_result("t1"),
        _assistant_text("text"),
        _user_text("turn 2"),
        _assistant_tool_use("t2"),
        _user_tool_result("t2"),
        _assistant_text("done"),
    ]
    older, newer = split_preserving_tool_pairs(messages, preserve_recent=4)
    combined = [*older, *newer]
    assert sanitize_conversation_messages(combined) == combined
