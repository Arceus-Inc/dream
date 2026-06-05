"""Spec 03 stage 1 — tool-call atom enforcement.

The atom is invariant #1 in Spec 00: a ``ToolUseBlock`` and its matching
``ToolResultBlock`` are an indivisible unit. The engine must:

- never send a transcript with a trailing unmatched ``tool_use`` to a
  provider (it 400s);
- detect a transcript ending in unconsumed ``tool_result``s on resume and
  re-enter the model, rather than treating the turn as complete or
  discarding the owed model turn.

These tests pin the two pure functions that enforce both halves of that
rule: ``sanitize_conversation_messages`` (the trimming half) and
``has_pending_continuation`` (the resume-detection half). Spec 03
acceptance criteria #9 and #10.
"""

from __future__ import annotations

from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    has_pending_continuation,
    sanitize_conversation_messages,
)

# --- helpers ------------------------------------------------------------------


def _user_text(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _asst_text(text: str) -> ConversationMessage:
    return ConversationMessage(role="assistant", content=[TextBlock(text=text)])


def _asst_tool_use(use_id: str, name: str = "read") -> ConversationMessage:
    return ConversationMessage(
        role="assistant",
        content=[ToolUseBlock(id=use_id, name=name, input={"path": "/x"})],
    )


def _user_tool_result(use_id: str, content: str = "ok") -> ConversationMessage:
    return ConversationMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id=use_id, content=content)],
    )


# --- sanitize_conversation_messages ------------------------------------------


def test_sanitize_empty_list() -> None:
    assert sanitize_conversation_messages([]) == []


def test_sanitize_preserves_clean_text_exchange() -> None:
    msgs = [_user_text("hi"), _asst_text("hello")]
    assert sanitize_conversation_messages(msgs) == msgs


def test_sanitize_preserves_complete_tool_round() -> None:
    """user → assistant(tool_use) → user(tool_result) → assistant(text) is balanced."""
    msgs = [
        _user_text("read the file"),
        _asst_tool_use("t1"),
        _user_tool_result("t1"),
        _asst_text("here it is"),
    ]
    assert sanitize_conversation_messages(msgs) == msgs


def test_sanitize_drops_empty_assistant_message() -> None:
    """Effectively-empty assistant messages corrupt resumed transcripts."""
    msgs = [
        _user_text("hi"),
        ConversationMessage(role="assistant", content=[]),
        _asst_text("hello"),
    ]
    result = sanitize_conversation_messages(msgs)
    assert result == [_user_text("hi"), _asst_text("hello")]


def test_sanitize_drops_whitespace_only_assistant_message() -> None:
    msgs = [
        _user_text("hi"),
        ConversationMessage(role="assistant", content=[TextBlock(text="  \n ")]),
    ]
    result = sanitize_conversation_messages(msgs)
    assert result == [_user_text("hi")]


def test_sanitize_trims_trailing_unmatched_tool_use() -> None:
    """The canonical interrupted-mid-turn corruption case (OpenHarness reference)."""
    msgs = [
        _user_text("read it"),
        _asst_tool_use("t1"),  # provider streamed tool_use but tool never ran
    ]
    result = sanitize_conversation_messages(msgs)
    assert result == [_user_text("read it")]


def test_sanitize_preserves_trailing_assistant_when_tool_use_is_matched() -> None:
    """Tool-use that *has* a matching tool_result in a later user msg stays."""
    msgs = [
        _user_text("read it"),
        _asst_tool_use("t1"),
        _user_tool_result("t1"),
    ]
    # The trailing user(tool_result) is fine — model owes a turn, but no trim.
    assert sanitize_conversation_messages(msgs) == msgs


def test_sanitize_returns_new_list_does_not_mutate_input() -> None:
    original = [
        _user_text("hi"),
        ConversationMessage(role="assistant", content=[]),
    ]
    snapshot = list(original)
    _ = sanitize_conversation_messages(original)
    assert original == snapshot  # input untouched


def test_sanitize_is_idempotent() -> None:
    """sanitize(sanitize(x)) == sanitize(x) — never repairs more on a second pass."""
    msgs = [
        _user_text("hi"),
        ConversationMessage(role="assistant", content=[]),
        _asst_tool_use("t1"),  # trailing unmatched
    ]
    once = sanitize_conversation_messages(msgs)
    twice = sanitize_conversation_messages(once)
    assert once == twice


def test_sanitize_preserves_mid_transcript_assistant_text_after_tool_round() -> None:
    """Mid-transcript completeness isn't this function's job — only the tail."""
    msgs = [
        _user_text("a"),
        _asst_tool_use("t1"),
        _user_tool_result("t1"),
        _asst_text("ok"),
        _user_text("b"),
        _asst_text("done"),
    ]
    assert sanitize_conversation_messages(msgs) == msgs


def test_sanitize_handles_assistant_with_text_and_unmatched_tool_use_at_tail() -> None:
    """Mixed-content trailing assistant message: still trim — the tool_use is owed."""
    msgs = [
        _user_text("a"),
        ConversationMessage(
            role="assistant",
            content=[TextBlock(text="thinking..."), ToolUseBlock(id="t1", name="x", input={})],
        ),
    ]
    result = sanitize_conversation_messages(msgs)
    assert result == [_user_text("a")]


# --- has_pending_continuation -------------------------------------------------


def test_pending_continuation_false_on_empty() -> None:
    assert has_pending_continuation([]) is False


def test_pending_continuation_false_when_ending_in_assistant_text() -> None:
    msgs = [_user_text("hi"), _asst_text("hello")]
    assert has_pending_continuation(msgs) is False


def test_pending_continuation_false_when_ending_in_user_text() -> None:
    msgs = [_user_text("hi")]
    assert has_pending_continuation(msgs) is False


def test_pending_continuation_true_when_last_user_carries_tool_results() -> None:
    """The crash-resume entry point: tool ran, results are in, model owes a turn."""
    msgs = [
        _user_text("read it"),
        _asst_tool_use("t1"),
        _user_tool_result("t1"),
    ]
    assert has_pending_continuation(msgs) is True


def test_pending_continuation_false_when_assistant_turn_already_consumed_results() -> None:
    """After the model has produced a turn following the tool_results, no continuation owed."""
    msgs = [
        _user_text("read it"),
        _asst_tool_use("t1"),
        _user_tool_result("t1"),
        _asst_text("here it is"),
    ]
    assert has_pending_continuation(msgs) is False


def test_pending_continuation_true_for_multiple_parallel_tool_results() -> None:
    """An assistant turn with two tool_uses → user message with both tool_results → continuation."""
    msgs = [
        _user_text("read both"),
        ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(id="t1", name="read", input={"path": "/a"}),
                ToolUseBlock(id="t2", name="read", input={"path": "/b"}),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="t1", content="A"),
                ToolResultBlock(tool_use_id="t2", content="B"),
            ],
        ),
    ]
    assert has_pending_continuation(msgs) is True


def test_pending_continuation_false_when_last_user_has_only_text() -> None:
    """A plain user follow-up after a complete tool round is a new turn, not a continuation."""
    msgs = [
        _user_text("read it"),
        _asst_tool_use("t1"),
        _user_tool_result("t1"),
        _asst_text("ok"),
        _user_text("now do another thing"),
    ]
    assert has_pending_continuation(msgs) is False


def test_sanitize_then_pending_continuation_agrees() -> None:
    """After trimming a dangling tool_use, the resume predicate must be False."""
    msgs = [
        _user_text("read it"),
        _asst_tool_use("t1"),  # trailing, never got a result
    ]
    cleaned = sanitize_conversation_messages(msgs)
    assert has_pending_continuation(cleaned) is False
