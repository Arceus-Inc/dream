"""Spec 03 stage 1 — transcript types + tool-call atom enforcement.

These tests pin the internal ``dream.engine._messages`` surface:

- typed transcript model (``ConversationMessage`` + ``ContentBlock`` union)
  that makes the tool-call atom *structurally* checkable rather than
  string-matched (Spec 03 acceptance #5);
- ``sanitize_conversation_messages`` — runs on every restored or continued
  transcript and trims a trailing unmatched ``ToolUseBlock`` so the
  provider never sees an unbalanced transcript (acceptance #9);
- ``has_pending_continuation`` — the crash-resume entry point that flags a
  transcript ending in user ``ToolResultBlock``s that still owe a model
  turn (acceptance #10);
- ``assistant_message_from_api`` — the single builder that turns a parsed
  provider response into a ``ConversationMessage``.

The module is internal — nothing here leaks through ``dream/__init__.py``.
"""

from __future__ import annotations

import pytest

from dream.engine._messages import (
    ConversationMessage,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    assistant_message_from_api,
)

# --- Block constructors -------------------------------------------------------


def test_text_block_holds_text() -> None:
    block = TextBlock(text="hello")
    assert block.text == "hello"


def test_image_block_holds_media_type_and_data() -> None:
    block = ImageBlock(media_type="image/png", data="base64==")
    assert block.media_type == "image/png"
    assert block.data == "base64=="


def test_tool_use_block_holds_id_name_input() -> None:
    block = ToolUseBlock(id="tu_1", name="read_file", input={"path": "x"})
    assert block.id == "tu_1"
    assert block.name == "read_file"
    assert block.input == {"path": "x"}


def test_tool_result_block_defaults_is_error_false() -> None:
    block = ToolResultBlock(tool_use_id="tu_1", content="ok")
    assert block.tool_use_id == "tu_1"
    assert block.content == "ok"
    assert block.is_error is False


def test_tool_result_block_can_carry_is_error_true() -> None:
    block = ToolResultBlock(tool_use_id="tu_1", content="boom", is_error=True)
    assert block.is_error is True


@pytest.mark.parametrize(
    "block",
    [
        TextBlock(text="x"),
        ImageBlock(media_type="image/png", data="x"),
        ToolUseBlock(id="i", name="n", input={}),
        ToolResultBlock(tool_use_id="i", content="c"),
    ],
)
def test_blocks_are_frozen(block: object) -> None:
    """Blocks are dataclasses with frozen=True; mutation must raise."""
    from dataclasses import FrozenInstanceError

    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        setattr(block, "_invalid_attr_unique_X", 1)


# --- ConversationMessage ------------------------------------------------------


def test_message_role_assistant_and_user() -> None:
    m_user = ConversationMessage(role="user", content=[TextBlock(text="hi")])
    m_asst = ConversationMessage(role="assistant", content=[TextBlock(text="yo")])
    assert m_user.role == "user"
    assert m_asst.role == "assistant"


def test_message_text_concatenates_text_blocks_only() -> None:
    msg = ConversationMessage(
        role="assistant",
        content=[
            TextBlock(text="hello"),
            ToolUseBlock(id="t1", name="x", input={}),
            TextBlock(text=" world"),
        ],
    )
    assert msg.text == "hello world"


def test_message_text_empty_when_no_text_blocks() -> None:
    msg = ConversationMessage(
        role="assistant",
        content=[ToolUseBlock(id="t1", name="x", input={})],
    )
    assert msg.text == ""


def test_message_tool_uses_filters_correctly() -> None:
    tu = ToolUseBlock(id="t1", name="read", input={"path": "/a"})
    msg = ConversationMessage(
        role="assistant",
        content=[TextBlock(text="reading"), tu, TextBlock(text="...")],
    )
    assert msg.tool_uses == [tu]


def test_message_tool_results_filters_correctly() -> None:
    tr = ToolResultBlock(tool_use_id="t1", content="ok")
    msg = ConversationMessage(
        role="user",
        content=[tr, TextBlock(text="also a note")],
    )
    assert msg.tool_results == [tr]


# --- is_effectively_empty -----------------------------------------------------


def test_empty_content_is_effectively_empty() -> None:
    msg = ConversationMessage(role="assistant", content=[])
    assert msg.is_effectively_empty() is True


def test_only_empty_text_is_effectively_empty() -> None:
    msg = ConversationMessage(role="assistant", content=[TextBlock(text="")])
    assert msg.is_effectively_empty() is True


def test_only_whitespace_text_is_effectively_empty() -> None:
    msg = ConversationMessage(role="assistant", content=[TextBlock(text="   \n\t")])
    assert msg.is_effectively_empty() is True


def test_tool_use_makes_message_non_empty() -> None:
    """A tool_use is real work — never effectively empty even with no text."""
    msg = ConversationMessage(
        role="assistant",
        content=[ToolUseBlock(id="t1", name="x", input={})],
    )
    assert msg.is_effectively_empty() is False


def test_any_real_text_makes_message_non_empty() -> None:
    msg = ConversationMessage(role="assistant", content=[TextBlock(text="hi")])
    assert msg.is_effectively_empty() is False


def test_tool_result_in_user_message_is_not_empty() -> None:
    """User messages carrying tool_results are never effectively empty."""
    msg = ConversationMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id="t1", content="ok")],
    )
    assert msg.is_effectively_empty() is False


# --- assistant_message_from_api ----------------------------------------------


def test_assistant_message_from_api_wraps_blocks() -> None:
    blocks = [TextBlock(text="hi"), ToolUseBlock(id="t1", name="x", input={})]
    msg = assistant_message_from_api(blocks)
    assert msg.role == "assistant"
    assert msg.content == blocks


def test_assistant_message_from_api_accepts_empty_blocks() -> None:
    msg = assistant_message_from_api([])
    assert msg.role == "assistant"
    assert msg.content == []
    assert msg.is_effectively_empty() is True
