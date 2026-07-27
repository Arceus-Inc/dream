"""Spec 04 stage 4b — checkpoints, PTL collapse, head truncation, CompactionResult.

Covers:
- ``CompactionResult`` dataclass shape (the result the orchestrator hands to the engine).
- ``record_compact_checkpoint`` — carryover metadata grows a recoverable trail.
- ``try_context_collapse`` — deterministic text-block shrink before paying for full LLM.
- ``truncate_head_for_ptl_retry`` — drop oldest prompt rounds when reactive
  (prompt-too-long) compaction needs aggressive room.
"""

from __future__ import annotations

from dataclasses import is_dataclass

from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.services.compact import (
    CompactionResult,
    record_compact_checkpoint,
    split_preserving_tool_pairs,
    truncate_head_for_ptl_retry,
    try_context_collapse,
)
from dream.services.compact._carryover_state import CarryoverMetadata


def _user_text(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _assistant_text(text: str) -> ConversationMessage:
    return ConversationMessage(role="assistant", content=[TextBlock(text=text)])


# --- CompactionResult shape -------------------------------------------------


def test_compaction_result_is_dataclass_with_required_fields() -> None:
    """The result the engine will branch on must carry a stable shape."""
    assert is_dataclass(CompactionResult)
    boundary = _user_text("marker")
    result = CompactionResult(
        trigger="auto",
        tier="microcompact",
        boundary_marker=boundary,
        summary_messages=[],
        messages_to_keep=[],
        attachments=[],
        metadata={},
    )
    assert result.trigger == "auto"
    assert result.tier == "microcompact"
    assert result.boundary_marker is boundary


# --- record_compact_checkpoint ----------------------------------------------


def test_record_checkpoint_creates_list_when_missing() -> None:
    carryover = CarryoverMetadata()
    record_compact_checkpoint(
        carryover,
        checkpoint="post_microcompact",
        trigger="auto",
        message_count=12,
        token_count=8_000,
    )
    assert len(carryover.compact_checkpoints) == 1


def test_record_checkpoint_appends_in_order() -> None:
    carryover = CarryoverMetadata()
    for name in ("pre", "post_microcompact", "post_full"):
        record_compact_checkpoint(
            carryover,
            checkpoint=name,
            trigger="auto",
            message_count=10,
            token_count=1_000,
        )
    names = [entry.checkpoint for entry in carryover.compact_checkpoints]
    assert names == ["pre", "post_microcompact", "post_full"]


def test_record_checkpoint_sets_compact_last_to_latest() -> None:
    carryover = CarryoverMetadata()
    record_compact_checkpoint(
        carryover, checkpoint="first", trigger="auto", message_count=1, token_count=1
    )
    record_compact_checkpoint(
        carryover, checkpoint="latest", trigger="manual", message_count=2, token_count=2
    )
    assert carryover.compact_last is not None
    assert carryover.compact_last.checkpoint == "latest"
    assert carryover.compact_last.trigger == "manual"


def test_record_checkpoint_includes_optional_attempt_and_details() -> None:
    carryover = CarryoverMetadata()
    record_compact_checkpoint(
        carryover,
        checkpoint="ptl_retry",
        trigger="reactive",
        message_count=5,
        token_count=2_500,
        attempt=1,
        details={"reason": "ptl"},
    )
    entry = carryover.compact_checkpoints[0]
    assert entry.attempt == 1
    assert entry.details["reason"] == "ptl"


def test_record_checkpoint_handles_none_carryover_gracefully() -> None:
    """Caller may pass None when no persistent metadata is in play."""
    payload = record_compact_checkpoint(
        None,
        checkpoint="pre",
        trigger="auto",
        message_count=1,
        token_count=1,
    )
    assert payload.checkpoint == "pre"
    assert payload.trigger == "auto"


# --- try_context_collapse ----------------------------------------------------


def test_context_collapse_returns_none_for_short_transcript() -> None:
    messages = [_user_text("hi"), _assistant_text("hello")]
    assert try_context_collapse(messages, preserve_recent=5) is None


def test_context_collapse_returns_none_when_no_block_is_large_enough() -> None:
    messages = [
        _user_text("small1"),
        _assistant_text("small2"),
        _user_text("small3"),
        _assistant_text("small4"),
        _user_text("small5"),
        _assistant_text("small6"),
    ]
    assert try_context_collapse(messages, preserve_recent=2) is None


def test_context_collapse_shrinks_old_oversize_text_block() -> None:
    big = "L" * 10_000
    messages = [
        _user_text(big),
        _assistant_text("reply"),
        _user_text("turn 2"),
        _assistant_text("reply 2"),
        _user_text("turn 3"),
        _assistant_text("reply 3"),
    ]
    out = try_context_collapse(messages, preserve_recent=2)
    assert out is not None
    assert isinstance(out[0].content[0], TextBlock)
    assert len(out[0].content[0].text) < len(big)
    # Marker explains the shrink
    assert "collapsed" in out[0].content[0].text


def test_context_collapse_does_not_touch_preserved_recent() -> None:
    big = "L" * 10_000
    recent_text = "RECENT-TURN"
    messages = [
        _user_text(big),
        _assistant_text("reply"),
        _user_text("filler"),
        _assistant_text("filler"),
        _user_text(recent_text),
        _assistant_text("reply"),
    ]
    out = try_context_collapse(messages, preserve_recent=2)
    assert out is not None
    assert recent_text in out[-2].text


def test_context_collapse_shrinks_old_tool_result_content() -> None:
    big = "X" * 10_000
    messages = [
        ConversationMessage(
            role="assistant", content=[ToolUseBlock(id="t1", name="read_file", input={})]
        ),
        ConversationMessage(
            role="user", content=[ToolResultBlock(tool_use_id="t1", content=big)]
        ),
        _user_text("turn 2"),
        _assistant_text("reply 2"),
        _user_text("turn 3"),
        _assistant_text("reply 3"),
    ]
    out = try_context_collapse(messages, preserve_recent=2)
    assert out is not None
    result_block = out[1].content[0]
    assert isinstance(result_block, ToolResultBlock)
    assert len(result_block.content) < len(big)


# --- truncate_head_for_ptl_retry --------------------------------------------


def test_truncate_head_returns_none_for_single_round() -> None:
    messages = [_user_text("only turn"), _assistant_text("only reply")]
    assert truncate_head_for_ptl_retry(messages) is None


def test_truncate_head_drops_oldest_rounds_for_multi_round() -> None:
    rounds = []
    for i in range(10):
        rounds.append(_user_text(f"turn-{i}"))
        rounds.append(_assistant_text(f"reply-{i}"))
    out = truncate_head_for_ptl_retry(rounds)
    assert out is not None
    assert len(out) < len(rounds)
    # At least the oldest user turn is gone
    texts = "\n".join(m.text for m in out)
    assert "turn-0" not in texts


def test_truncate_head_preserves_most_recent_round() -> None:
    rounds = []
    for i in range(5):
        rounds.append(_user_text(f"turn-{i}"))
        rounds.append(_assistant_text(f"reply-{i}"))
    out = truncate_head_for_ptl_retry(rounds)
    assert out is not None
    texts = "\n".join(m.text for m in out)
    assert "turn-4" in texts
    assert "reply-4" in texts


def test_truncate_head_prepends_marker_when_starts_with_assistant() -> None:
    """If truncation leaves an assistant-first transcript, prepend a user marker."""
    rounds = [
        _user_text("turn-0"),
        _assistant_text("reply-0"),
        _assistant_text("reply-1-orphan"),
        _user_text("turn-2"),
        _assistant_text("reply-2"),
        _user_text("turn-3"),
        _assistant_text("reply-3"),
    ]
    out = truncate_head_for_ptl_retry(rounds)
    assert out is not None
    assert out[0].role == "user"


# --- split_preserving_tool_pairs: negative preserve_recent clamp -------------


def test_split_clamps_negative_preserve_recent_no_indexerror() -> None:
    """A negative preserve_recent must be clamped to 0, not push split_index
    out of bounds (IndexError on boundary_crosses_tool_pair).
    """
    msgs = [
        _user_text("a"),
        _assistant_text("b"),
        _user_text("c"),
    ]
    older, newer = split_preserving_tool_pairs(msgs, preserve_recent=-5)
    # preserve_recent clamped to 0 => everything is "older", nothing preserved.
    assert older == msgs
    assert newer == []


# --- truncate_head re-sanitizes a dangling tool_use -------------------------


def test_truncate_head_resanitizes_orphan_tool_use_tail() -> None:
    """If dropping head rounds leaves the retained tail ending on an assistant
    ToolUseBlock with no matching result, truncate must re-sanitize it away so
    the next provider call is valid.
    """
    rounds: list[ConversationMessage] = []
    # Several complete prompt rounds so drop_count >= 1.
    for i in range(6):
        rounds.append(_user_text(f"turn-{i}"))
        rounds.append(_assistant_text(f"reply-{i}"))
    # Tail: an assistant tool_use with NO following tool_result (orphan).
    rounds.append(
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="orphan", name="bash", input={})],
        )
    )
    out = truncate_head_for_ptl_retry(rounds)
    assert out is not None
    # No trailing orphan tool_use may survive the boundary.
    assert not (out[-1].role == "assistant" and out[-1].tool_uses)
