"""Spec 03 stage 3c — OpenAI-compatible ``TurnStreamer`` adapter.

The adapter takes any OpenAI Chat Completions streaming source (real
or fake) and produces the engine's typed ``StreamEvent`` taxonomy:

- ``AssistantTextDelta`` per content delta as they arrive.
- A single ``AssistantTurnComplete(blocks, usage)`` at the end, where
  ``blocks`` is the assembled mix of ``TextBlock`` and ``ToolUseBlock``
  the assistant produced and ``usage`` is the final ``UsageSnapshot``.

These tests are pure-Python with no network: the adapter is constructed
with a ``stream_chat_completion`` callable that returns an async iterator
of OpenAI chunk dicts. The httpx-backed production builder is exercised
separately by a smoke test.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import pytest

from dream.engine._adapter_openai import (
    NoOpDispatcher,
    OpenAIChatStreamer,
    conversation_to_openai_messages,
)
from dream.engine._cost import UsageSnapshot
from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StreamEvent,
)
from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

# --- helpers ----------------------------------------------------------------


def _scripted(chunks: list[dict[str, Any]]):
    """Return a stream_chat_completion(messages, model) coroutine factory."""

    async def stream(
        messages: Sequence[dict[str, Any]], model: str
    ) -> AsyncIterator[dict[str, Any]]:
        async def _iter() -> AsyncIterator[dict[str, Any]]:
            for c in chunks:
                yield c

        return _iter()

    return stream


async def _drain(adapter: OpenAIChatStreamer) -> list[StreamEvent]:
    msgs = [ConversationMessage(role="user", content=[TextBlock(text="hi")])]
    out: list[StreamEvent] = []
    async for ev in adapter.stream_turn(msgs):
        out.append(ev)
    return out


def _text_chunk(text: str, finish: str | None = None) -> dict[str, Any]:
    return {
        "choices": [
            {"delta": {"content": text}, "finish_reason": finish, "index": 0}
        ]
    }


def _tool_call_chunk(
    *,
    index: int,
    id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
    finish: str | None = None,
) -> dict[str, Any]:
    tc: dict[str, Any] = {"index": index}
    if id is not None:
        tc["id"] = id
    fn: dict[str, Any] = {}
    if name is not None:
        fn["name"] = name
    if arguments is not None:
        fn["arguments"] = arguments
    if fn:
        tc["function"] = fn
    return {
        "choices": [
            {"delta": {"tool_calls": [tc]}, "finish_reason": finish, "index": 0}
        ]
    }


def _usage_chunk(prompt: int, completion: int) -> dict[str, Any]:
    return {
        "choices": [],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


# --- conversation_to_openai_messages translation ---------------------------


def test_translation_user_text_becomes_user_role_string_content() -> None:
    msgs = [ConversationMessage(role="user", content=[TextBlock(text="hello")])]
    out = conversation_to_openai_messages(msgs)
    assert out == [{"role": "user", "content": "hello"}]


def test_translation_assistant_text_becomes_assistant_role() -> None:
    msgs = [
        ConversationMessage(role="user", content=[TextBlock(text="hi")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="hey")]),
    ]
    out = conversation_to_openai_messages(msgs)
    assert out == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hey"},
    ]


def test_translation_assistant_tool_use_becomes_tool_calls() -> None:
    msgs = [
        ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="I'll look that up."),
                ToolUseBlock(id="call_1", name="lookup", input={"q": "x"}),
            ],
        )
    ]
    out = conversation_to_openai_messages(msgs)
    assert len(out) == 1
    msg = out[0]
    assert msg["role"] == "assistant"
    assert msg["content"] == "I'll look that up."
    assert msg["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "lookup",
                "arguments": json.dumps({"q": "x"}),
            },
        }
    ]


def test_translation_assistant_tool_use_without_text_has_null_content() -> None:
    msgs = [
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="call_x", name="ping", input={})],
        )
    ]
    out = conversation_to_openai_messages(msgs)
    assert out[0]["content"] is None
    assert out[0]["tool_calls"][0]["function"]["name"] == "ping"


def test_translation_user_tool_results_split_into_tool_role_messages() -> None:
    msgs = [
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="call_1", content="ok"),
                ToolResultBlock(
                    tool_use_id="call_2", content="bad", is_error=True
                ),
            ],
        )
    ]
    out = conversation_to_openai_messages(msgs)
    assert out == [
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        {"role": "tool", "tool_call_id": "call_2", "content": "bad"},
    ]


def test_translation_user_mixed_text_and_tool_result_splits_correctly() -> None:
    msgs = [
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="call_1", content="42"),
                TextBlock(text="and also..."),
            ],
        )
    ]
    out = conversation_to_openai_messages(msgs)
    # Tool results come first as tool-role messages; remaining text
    # becomes a follow-up user message.
    assert out == [
        {"role": "tool", "tool_call_id": "call_1", "content": "42"},
        {"role": "user", "content": "and also..."},
    ]


def test_translation_includes_system_prompt_when_provided() -> None:
    msgs = [ConversationMessage(role="user", content=[TextBlock(text="hi")])]
    out = conversation_to_openai_messages(msgs, system_prompt="be helpful")
    assert out[0] == {"role": "system", "content": "be helpful"}
    assert out[1] == {"role": "user", "content": "hi"}


def test_translation_empty_messages_yields_empty_or_system_only() -> None:
    assert conversation_to_openai_messages([]) == []
    assert conversation_to_openai_messages([], system_prompt="hi") == [
        {"role": "system", "content": "hi"}
    ]


# --- streaming: text only ---------------------------------------------------


async def test_stream_text_only_yields_deltas_then_complete() -> None:
    adapter = OpenAIChatStreamer(
        stream_chat_completion=_scripted(
            [
                _text_chunk("Hel"),
                _text_chunk("lo "),
                _text_chunk("world", finish="stop"),
                _usage_chunk(10, 3),
            ]
        ),
        model="gpt-test",
    )
    events = await _drain(adapter)

    deltas = [e for e in events if isinstance(e, AssistantTextDelta)]
    completes = [e for e in events if isinstance(e, AssistantTurnComplete)]
    assert [d.text for d in deltas] == ["Hel", "lo ", "world"]
    assert len(completes) == 1
    comp = completes[0]
    assert len(comp.blocks) == 1
    assert isinstance(comp.blocks[0], TextBlock)
    assert comp.blocks[0].text == "Hello world"
    assert comp.usage == UsageSnapshot(input_tokens=10, output_tokens=3)


async def test_stream_empty_text_still_yields_complete_with_zero_blocks() -> None:
    adapter = OpenAIChatStreamer(
        stream_chat_completion=_scripted(
            [
                _text_chunk("", finish="stop"),
                _usage_chunk(5, 0),
            ]
        ),
        model="gpt-test",
    )
    events = await _drain(adapter)
    completes = [e for e in events if isinstance(e, AssistantTurnComplete)]
    assert len(completes) == 1
    # An empty assistant turn drops the TextBlock entirely rather than
    # appending an empty one (sanitize_conversation_messages would later
    # treat it as effectively empty anyway).
    assert completes[0].blocks == []


async def test_stream_complete_is_terminal_no_events_after() -> None:
    adapter = OpenAIChatStreamer(
        stream_chat_completion=_scripted(
            [_text_chunk("ok", finish="stop"), _usage_chunk(1, 1)]
        ),
        model="gpt-test",
    )
    events = await _drain(adapter)
    assert isinstance(events[-1], AssistantTurnComplete)


# --- streaming: tool calls --------------------------------------------------


async def test_stream_single_tool_call_accumulates_arguments_and_blocks() -> None:
    adapter = OpenAIChatStreamer(
        stream_chat_completion=_scripted(
            [
                _tool_call_chunk(
                    index=0, id="call_1", name="lookup", arguments='{"q"'
                ),
                _tool_call_chunk(index=0, arguments=': "weather"}'),
                {
                    "choices": [
                        {"delta": {}, "finish_reason": "tool_calls", "index": 0}
                    ]
                },
                _usage_chunk(15, 4),
            ]
        ),
        model="gpt-test",
    )
    events = await _drain(adapter)
    comp = next(e for e in events if isinstance(e, AssistantTurnComplete))
    tool_blocks = [b for b in comp.blocks if isinstance(b, ToolUseBlock)]
    assert len(tool_blocks) == 1
    assert tool_blocks[0].id == "call_1"
    assert tool_blocks[0].name == "lookup"
    assert tool_blocks[0].input == {"q": "weather"}
    # No text was emitted.
    assert not [b for b in comp.blocks if isinstance(b, TextBlock)]


async def test_stream_multiple_tool_calls_indexed_independently() -> None:
    adapter = OpenAIChatStreamer(
        stream_chat_completion=_scripted(
            [
                _tool_call_chunk(
                    index=0, id="c1", name="a", arguments='{"x": 1}'
                ),
                _tool_call_chunk(
                    index=1, id="c2", name="b", arguments='{"y": 2}'
                ),
                {
                    "choices": [
                        {"delta": {}, "finish_reason": "tool_calls", "index": 0}
                    ]
                },
                _usage_chunk(20, 5),
            ]
        ),
        model="gpt-test",
    )
    events = await _drain(adapter)
    comp = next(e for e in events if isinstance(e, AssistantTurnComplete))
    tool_blocks = [b for b in comp.blocks if isinstance(b, ToolUseBlock)]
    assert len(tool_blocks) == 2
    assert tool_blocks[0].id == "c1"
    assert tool_blocks[0].name == "a"
    assert tool_blocks[0].input == {"x": 1}
    assert tool_blocks[1].id == "c2"
    assert tool_blocks[1].name == "b"
    assert tool_blocks[1].input == {"y": 2}


async def test_stream_text_then_tool_call_orders_blocks_text_first() -> None:
    adapter = OpenAIChatStreamer(
        stream_chat_completion=_scripted(
            [
                _text_chunk("Let me check."),
                _tool_call_chunk(
                    index=0, id="c1", name="check", arguments='{"x": true}'
                ),
                {
                    "choices": [
                        {"delta": {}, "finish_reason": "tool_calls", "index": 0}
                    ]
                },
                _usage_chunk(8, 3),
            ]
        ),
        model="gpt-test",
    )
    events = await _drain(adapter)
    comp = next(e for e in events if isinstance(e, AssistantTurnComplete))
    assert len(comp.blocks) == 2
    assert isinstance(comp.blocks[0], TextBlock)
    assert comp.blocks[0].text == "Let me check."
    assert isinstance(comp.blocks[1], ToolUseBlock)


async def test_stream_tool_call_with_empty_arguments_defaults_to_empty_dict() -> None:
    adapter = OpenAIChatStreamer(
        stream_chat_completion=_scripted(
            [
                _tool_call_chunk(index=0, id="c1", name="ping", arguments=""),
                {
                    "choices": [
                        {"delta": {}, "finish_reason": "tool_calls", "index": 0}
                    ]
                },
                _usage_chunk(2, 1),
            ]
        ),
        model="gpt-test",
    )
    events = await _drain(adapter)
    comp = next(e for e in events if isinstance(e, AssistantTurnComplete))
    tool_blocks = [b for b in comp.blocks if isinstance(b, ToolUseBlock)]
    assert tool_blocks[0].input == {}


# --- usage extraction -------------------------------------------------------


async def test_stream_usage_defaults_to_zero_when_missing() -> None:
    adapter = OpenAIChatStreamer(
        stream_chat_completion=_scripted(
            [_text_chunk("ok", finish="stop")]
        ),
        model="gpt-test",
    )
    events = await _drain(adapter)
    comp = next(e for e in events if isinstance(e, AssistantTurnComplete))
    assert comp.usage == UsageSnapshot()


async def test_stream_passes_model_through_to_callable() -> None:
    seen_model: list[str] = []

    async def stream(
        messages: Sequence[dict[str, Any]], model: str
    ) -> AsyncIterator[dict[str, Any]]:
        seen_model.append(model)

        async def _iter() -> AsyncIterator[dict[str, Any]]:
            yield _text_chunk("ok", finish="stop")

        return _iter()

    adapter = OpenAIChatStreamer(
        stream_chat_completion=stream, model="claude-4.7"
    )
    await _drain(adapter)
    assert seen_model == ["claude-4.7"]


async def test_stream_passes_translated_messages_to_callable() -> None:
    seen_messages: list[list[dict[str, Any]]] = []

    async def stream(
        messages: Sequence[dict[str, Any]], model: str
    ) -> AsyncIterator[dict[str, Any]]:
        seen_messages.append([dict(m) for m in messages])

        async def _iter() -> AsyncIterator[dict[str, Any]]:
            yield _text_chunk("ok", finish="stop")

        return _iter()

    adapter = OpenAIChatStreamer(
        stream_chat_completion=stream,
        model="gpt-test",
        system_prompt="be terse",
    )
    msgs = [ConversationMessage(role="user", content=[TextBlock(text="hi")])]
    out: list[StreamEvent] = []
    async for ev in adapter.stream_turn(msgs):
        out.append(ev)

    assert seen_messages == [
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ]
    ]


async def test_stream_applies_cache_control_when_prompt_cache_enabled() -> None:
    seen_messages: list[list[Mapping[str, object]]] = []

    async def stream(
        messages: Sequence[Mapping[str, object]], model: str
    ) -> AsyncIterator[dict[str, Any]]:
        seen_messages.append([dict(m) for m in messages])

        async def _iter() -> AsyncIterator[dict[str, Any]]:
            yield _text_chunk("ok", finish="stop")

        return _iter()

    adapter = OpenAIChatStreamer(
        stream_chat_completion=stream,
        model="gpt-test",
        system_prompt="be terse",
        prompt_cache=True,
    )
    msgs = [ConversationMessage(role="user", content=[TextBlock(text="hi")])]
    async for _ in adapter.stream_turn(msgs):
        pass

    system = seen_messages[0][0]
    content = system["content"]
    assert isinstance(content, list)
    assert content[0]["cache_control"] == {"type": "ephemeral"}


# --- NoOpDispatcher ---------------------------------------------------------


async def test_noop_dispatcher_raises_when_called() -> None:
    d = NoOpDispatcher()
    with pytest.raises(RuntimeError) as excinfo:
        await d.dispatch("anything", {})
    assert "no tools" in str(excinfo.value).lower()


# --- keep linters happy for fixtures referenced only via dynamic dispatch ---

_ = (ContentBlock,)


async def test_stream_tool_call_with_unparseable_arguments_is_dropped_with_error_event() -> None:
    """Malformed streamed JSON args must NOT become an empty-dict dispatch.

    Fabricating ``{}`` would invoke a tool the model never parameterised; the
    adapter instead surfaces a recoverable ``ErrorEvent`` and omits the call.
    """
    adapter = OpenAIChatStreamer(
        stream_chat_completion=_scripted(
            [
                _tool_call_chunk(index=0, id="c1", name="danger", arguments='{"path": "/et'),
                {"choices": [{"delta": {}, "finish_reason": "tool_calls", "index": 0}]},
                _usage_chunk(2, 1),
            ]
        ),
        model="gpt-test",
    )
    events = await _drain(adapter)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].recoverable is True
    assert "danger" in errors[0].message

    comp = next(e for e in events if isinstance(e, AssistantTurnComplete))
    tool_blocks = [b for b in comp.blocks if isinstance(b, ToolUseBlock)]
    assert tool_blocks == []  # the unparseable call was dropped, not dispatched


async def test_stream_incomplete_tool_call_dropped_with_error_event() -> None:
    """A truncated stream leaving an id-less/name-less tool call is dropped
    (with an ErrorEvent), never emitted as ToolUseBlock(id='', name='')."""
    adapter = OpenAIChatStreamer(
        stream_chat_completion=_scripted(
            [
                # function fragment with arguments but the id/name chunk never arrives
                {
                    "choices": [
                        {
                            "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]},
                            "finish_reason": None,
                            "index": 0,
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls", "index": 0}]},
                _usage_chunk(1, 1),
            ]
        ),
        model="gpt-test",
    )
    events = await _drain(adapter)
    assert any(isinstance(e, ErrorEvent) for e in events)
    comp = next(e for e in events if isinstance(e, AssistantTurnComplete))
    assert [b for b in comp.blocks if isinstance(b, ToolUseBlock)] == []


async def test_stream_non_numeric_tool_call_index_does_not_abort_turn() -> None:
    """A malformed (non-int) tool_call index skips that fragment rather than
    raising out of the turn."""
    adapter = OpenAIChatStreamer(
        stream_chat_completion=_scripted(
            [
                _text_chunk("hello"),
                {
                    "choices": [
                        {
                            "delta": {"tool_calls": [{"index": "bogus", "id": "c1"}]},
                            "finish_reason": None,
                            "index": 0,
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]},
                _usage_chunk(1, 1),
            ]
        ),
        model="gpt-test",
    )
    events = await _drain(adapter)  # must not raise
    comp = next(e for e in events if isinstance(e, AssistantTurnComplete))
    assert any(isinstance(b, TextBlock) for b in comp.blocks)
