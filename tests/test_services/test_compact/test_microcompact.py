"""Spec 04 stage 4b — microcompact: cheap-first tool-result content clearing.

Microcompact is the first tier of compaction: walk the transcript, find old
``ToolResultBlock``s whose content is safely droppable (large MCP results or
known-bulky local tools), and replace their *content* with a sentinel string
while keeping the structural shell intact. This reclaims tokens without an
LLM call and without touching the tool-call atom (Spec 00 invariant #1).
"""

from __future__ import annotations

import pytest

from dream.engine._messages import (
    ConversationMessage,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.services.compact import (
    COMPACTABLE_TOOLS,
    DEFAULT_KEEP_RECENT,
    TIME_BASED_MC_CLEARED_MESSAGE,
    collect_compactable_tool_ids,
    microcompact_messages,
)


def _tool_round(tool_id: str, name: str, content: str) -> list[ConversationMessage]:
    """Return a single tool round: assistant tool_use + user tool_result."""
    return [
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id=tool_id, name=name, input={})],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id=tool_id, content=content)],
        ),
    ]


# --- collect_compactable_tool_ids -------------------------------------------


def test_collect_returns_empty_for_no_messages() -> None:
    assert collect_compactable_tool_ids([]) == []


def test_collect_includes_known_compactable_local_tools() -> None:
    # Content must exceed the replacement sentinel; otherwise clearing it would
    # grow the transcript (negative compaction) — see the minimum-size gate.
    content = "some content " * 10
    assert len(content) > len(TIME_BASED_MC_CLEARED_MESSAGE)
    messages = _tool_round("t1", "read_file", content)
    assert collect_compactable_tool_ids(messages) == ["t1"]


def test_collect_includes_large_mcp_results_even_for_unknown_tool() -> None:
    """mcp__ prefix always microcompactable per is_microcompactable_tool_result."""
    messages = _tool_round("t1", "mcp__github__search", "small output")
    assert collect_compactable_tool_ids(messages) == ["t1"]


def test_collect_excludes_small_unknown_local_tool() -> None:
    messages = _tool_round("t1", "custom_tiny_tool", "hi")
    assert collect_compactable_tool_ids(messages) == []


def test_collect_preserves_chronological_order() -> None:
    messages: list[ConversationMessage] = []
    # Each payload must exceed the sentinel so the minimum-size gate keeps them.
    big = "x" * (len(TIME_BASED_MC_CLEARED_MESSAGE) + 20)
    for i, name in enumerate(["read_file", "bash", "grep"]):
        messages.extend(_tool_round(f"t{i}", name, big))
    assert collect_compactable_tool_ids(messages) == ["t0", "t1", "t2"]


def test_compactable_tools_set_includes_canonical_local_tools() -> None:
    """The frozenset is part of the public contract; regressions on file/bash break here."""
    for expected in ("read_file", "bash", "grep", "glob", "write_file", "apply_patch"):
        assert expected in COMPACTABLE_TOOLS


# --- microcompact_messages ---------------------------------------------------


def test_microcompact_default_keep_recent_is_five() -> None:
    """Spec leaves the number to implementation but it MUST be > 0."""
    assert DEFAULT_KEEP_RECENT >= 1


def test_microcompact_empty_input_is_noop() -> None:
    out, saved = microcompact_messages([])
    assert out == []
    assert saved == 0


def test_microcompact_few_items_below_keep_recent_is_noop() -> None:
    messages = _tool_round("t0", "read_file", "small")
    out, saved = microcompact_messages(messages, keep_recent=5)
    assert saved == 0
    # content unchanged
    result_block = out[1].content[0]
    assert isinstance(result_block, ToolResultBlock)
    assert result_block.content == "small"


def test_microcompact_drops_oldest_keeping_most_recent() -> None:
    messages: list[ConversationMessage] = []
    for i in range(8):
        messages.extend(_tool_round(f"t{i}", "read_file", f"content-{i}-{'x' * 100}"))
    out, saved = microcompact_messages(messages, keep_recent=3)
    # 3 most-recent kept verbatim; 5 oldest cleared
    cleared_ids = {f"t{i}" for i in range(5)}
    kept_ids = {f"t{i}" for i in range(5, 8)}
    for msg in out:
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                if block.tool_use_id in cleared_ids:
                    assert block.content == TIME_BASED_MC_CLEARED_MESSAGE
                elif block.tool_use_id in kept_ids:
                    assert block.content.startswith("content-")
    assert saved > 0


def test_microcompact_preserves_tool_use_blocks_on_assistant_messages() -> None:
    """The atom shell must stay; only result content drops."""
    messages: list[ConversationMessage] = []
    for i in range(8):
        messages.extend(_tool_round(f"t{i}", "read_file", "x" * 200))
    out, _ = microcompact_messages(messages, keep_recent=2)
    tool_use_ids = [
        block.id
        for msg in out
        if msg.role == "assistant"
        for block in msg.content
        if isinstance(block, ToolUseBlock)
    ]
    assert tool_use_ids == [f"t{i}" for i in range(8)]


def test_microcompact_keep_recent_zero_clamps_to_one() -> None:
    """Clearing ALL would leave no working context — clamp at 1."""
    messages: list[ConversationMessage] = []
    for i in range(4):
        messages.extend(_tool_round(f"t{i}", "read_file", "x" * 200))
    out, _ = microcompact_messages(messages, keep_recent=0)
    kept_intact = [
        block
        for msg in out
        for block in msg.content
        if isinstance(block, ToolResultBlock)
        and block.content != TIME_BASED_MC_CLEARED_MESSAGE
    ]
    assert len(kept_intact) == 1


def test_microcompact_does_not_double_clear() -> None:
    messages: list[ConversationMessage] = []
    for i in range(6):
        messages.extend(_tool_round(f"t{i}", "read_file", "x" * 200))
    once, saved_once = microcompact_messages(messages, keep_recent=2)
    _, saved_twice = microcompact_messages(once, keep_recent=2)
    assert saved_once > 0
    assert saved_twice == 0


def test_microcompact_does_not_mutate_input_list() -> None:
    """Dream style: pure functions return new lists; input messages survive verbatim."""
    messages: list[ConversationMessage] = []
    for i in range(6):
        messages.extend(_tool_round(f"t{i}", "read_file", "original-" + ("x" * 200)))
    original_contents = [
        block.content
        for msg in messages
        for block in msg.content
        if isinstance(block, ToolResultBlock)
    ]
    microcompact_messages(messages, keep_recent=2)
    after_contents = [
        block.content
        for msg in messages
        for block in msg.content
        if isinstance(block, ToolResultBlock)
    ]
    assert after_contents == original_contents


def test_microcompact_returns_token_savings_estimate() -> None:
    messages: list[ConversationMessage] = []
    for i in range(6):
        messages.extend(_tool_round(f"t{i}", "read_file", "x" * 400))
    _, saved = microcompact_messages(messages, keep_recent=1)
    # 5 results, ~100 tokens each (400 chars / 4) = ~500 tokens lower bound
    assert saved >= 100


@pytest.mark.parametrize("name", ["read_file", "bash", "grep", "glob", "web_fetch"])
def test_microcompact_handles_each_canonical_tool(name: str) -> None:
    messages: list[ConversationMessage] = []
    for i in range(8):
        messages.extend(_tool_round(f"t{i}", name, "x" * 200))
    _, saved = microcompact_messages(messages, keep_recent=2)
    assert saved > 0


# --- minimum-size gate (negative-compaction guard) ---------------------------


def test_tiny_canonical_payload_is_not_marked_compactable() -> None:
    """A canonical-tool result smaller than the replacement sentinel is NOT
    compactable — clearing it would *grow* the transcript (negative compaction).
    """
    tiny = "ok"  # far shorter than TIME_BASED_MC_CLEARED_MESSAGE
    assert len(tiny) < len(TIME_BASED_MC_CLEARED_MESSAGE)
    messages: list[ConversationMessage] = []
    for i in range(6):
        messages.extend(_tool_round(f"t{i}", "read_file", tiny))
    assert collect_compactable_tool_ids(messages) == []


def test_tiny_canonical_payload_microcompact_reports_no_savings() -> None:
    """Microcompacting tiny canonical payloads must not claim phantom savings
    nor replace short content with the longer sentinel.
    """
    tiny = "ok"
    messages: list[ConversationMessage] = []
    for i in range(6):
        messages.extend(_tool_round(f"t{i}", "read_file", tiny))
    out, saved = microcompact_messages(messages, keep_recent=1)
    assert saved == 0
    contents = [
        block.content
        for msg in out
        for block in msg.content
        if isinstance(block, ToolResultBlock)
    ]
    assert all(c == tiny for c in contents)


def test_large_canonical_payload_above_threshold_is_still_compactable() -> None:
    """Payloads larger than the sentinel remain compactable — the gate only
    excludes the negative-compaction case.
    """
    big = "x" * (len(TIME_BASED_MC_CLEARED_MESSAGE) + 50)
    messages: list[ConversationMessage] = []
    for i in range(6):
        messages.extend(_tool_round(f"t{i}", "read_file", big))
    assert len(collect_compactable_tool_ids(messages)) == 6
