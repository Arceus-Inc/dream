"""Eval: durable session save/resume across a simulated process restart."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    sanitize_conversation_messages,
)
from dream.services.session_store import FileSessionStore
from dream.session import Session, SessionOptions

pytestmark = pytest.mark.eval


def _block_kind(msg: ConversationMessage) -> list[str]:
    kinds: list[str] = []
    for block in msg.content:
        if isinstance(block, TextBlock):
            kinds.append("text")
        elif isinstance(block, ToolUseBlock):
            kinds.append(f"tool_use:{block.id}")
        elif isinstance(block, ToolResultBlock):
            kinds.append(f"tool_result:{block.tool_use_id}")
        else:
            kinds.append("other")
    return kinds


def test_eval_session_save_resume_cross_process(tmp_path: Path) -> None:
    """Build transcript with tool call, save, restore in new Session, assert parity."""
    store = FileSessionStore(tmp_path / "sessions")

    # Process A: build session with multi-turn transcript including a tool call.
    session_a = Session(
        id="eval-session-1",
        options=SessionOptions(model="eval-model", system_prompt="eval sys"),
    )
    session_a._transcript = [
        ConversationMessage(role="user", content=[TextBlock(text="turn-1")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="reply-1")]),
        ConversationMessage(role="user", content=[TextBlock(text="turn-2")]),
        ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="calling tool"),
                ToolUseBlock(id="tu_eval", name="lookup", input={"q": "test"}),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="tu_eval", content="found it", is_error=False)
            ],
        ),
        ConversationMessage(role="assistant", content=[TextBlock(text="final")]),
    ]
    session_a.cost.input_tokens = 100
    session_a.cost.output_tokens = 50
    session_a.cost.cache_read_tokens = 10
    session_a.cost.cache_write_tokens = 5
    session_a.cost.cost_usd = 0.01

    snapshot = session_a.snapshot()
    store.save(snapshot)

    # Process B: new Session, restore from disk.
    session_b = Session(id="eval-session-1", options=SessionOptions(model="eval-model"))
    loaded = store.load("eval-session-1")
    session_b.restore_from_snapshot(loaded)

    # Transcript equality: roles + block kinds + tool ids.
    assert len(session_b.transcript) == len(session_a.transcript)
    for orig, restored in zip(session_a.transcript, session_b.transcript, strict=True):
        assert orig.role == restored.role
        assert _block_kind(orig) == _block_kind(restored)

    # Cost equality.
    assert session_b.cost.input_tokens == session_a.cost.input_tokens
    assert session_b.cost.output_tokens == session_a.cost.output_tokens
    assert session_b.cost.cache_read_tokens == session_a.cost.cache_read_tokens
    assert session_b.cost.cache_write_tokens == session_a.cost.cache_write_tokens
    assert session_b.cost.cost_usd == session_a.cost.cost_usd

    # Sanitize leaves no dangling tool_use.
    sanitized = sanitize_conversation_messages(session_b.transcript)
    tool_use_ids = {
        block.id
        for msg in sanitized
        for block in msg.content
        if isinstance(block, ToolUseBlock)
    }
    tool_result_ids = {
        block.tool_use_id
        for msg in sanitized
        for block in msg.content
        if isinstance(block, ToolResultBlock)
    }
    assert tool_use_ids == tool_result_ids

    # Snapshot metadata preserved.
    assert loaded.model == "eval-model"
    assert loaded.system_prompt == "eval sys"
    assert loaded.saved_at <= datetime.now(tz=UTC)
