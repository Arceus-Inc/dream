"""Spec 04 Wave B — reactive PTL recovery in the act-loop."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest

from dream.engine._cost import UsageSnapshot
from dream.engine._events import AssistantTurnComplete, StatusEvent, StreamEvent
from dream.engine._loop import QueryContext, run_query
from dream.engine._messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from dream.services.compact import DEFAULT_KEEP_RECENT
from dream.services.compact._overflow import is_context_length_overflow


def _overflow_error(
    *,
    code: str = "context_length_exceeded",
    message: str = "maximum context length exceeded",
) -> httpx.HTTPStatusError:
    """Prefer structured ``error.code`` — the classifier's primary signal."""
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(
        400,
        request=request,
        json={"error": {"code": code, "message": message}},
    )
    return httpx.HTTPStatusError("context overflow", request=request, response=response)


class _OverflowThenOkStreamer:
    """Fails the first ``stream_turn`` with a PTL error; succeeds on retry."""

    def __init__(self) -> None:
        self.calls: list[list[ConversationMessage]] = []
        self._attempts = 0

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append([m for m in messages])
        self._attempts += 1
        if self._attempts == 1:
            raise _overflow_error()
        yield AssistantTurnComplete(
            blocks=[TextBlock(text="recovered")],
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        )


class _AlwaysOverflowStreamer:
    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        raise _overflow_error()
        yield AssistantTurnComplete(  # pragma: no cover — makes this an async generator
            blocks=[TextBlock(text="unreachable")],
            usage=UsageSnapshot(),
        )


class _MidStreamOverflowStreamer:
    """Yields one delta then fails — must not trigger PTL retry."""

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        from dream.engine._events import AssistantTextDelta

        yield AssistantTextDelta(text="partial")
        raise _overflow_error()


class _FakeDispatcher:
    async def dispatch(self, name: str, input: dict[str, Any]) -> tuple[str, bool]:
        return "ok", False


def test_is_context_length_overflow_detects_400_body() -> None:
    assert is_context_length_overflow(_overflow_error())


def test_is_context_length_overflow_rejects_auth_errors() -> None:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(401, request=request, json={"error": {"message": "invalid key"}})
    exc = httpx.HTTPStatusError("auth", request=request, response=response)
    assert not is_context_length_overflow(exc)


@pytest.mark.asyncio
async def test_run_query_ptl_shrinks_and_retries_once() -> None:
    streamer = _OverflowThenOkStreamer()
    ctx = QueryContext(
        client=streamer,
        tools=_FakeDispatcher(),
        max_turns=1,
        ptl_preserve_recent=DEFAULT_KEEP_RECENT,
    )
    big = "x" * 4096
    messages: list[ConversationMessage] = []
    for i in range(8):
        tu = f"tu_{i}"
        messages.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=tu, name="read_file", input={"path": "a"})],
            )
        )
        messages.append(
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id=tu, content=big, is_error=False)],
            )
        )
    messages.append(ConversationMessage(role="user", content=[TextBlock(text="go")]))

    events: list[StreamEvent] = []
    async for ev in run_query(ctx, messages):
        events.append(ev)

    assert len(streamer.calls) == 2
    assert len(streamer.calls[1]) <= len(streamer.calls[0])
    assert any(isinstance(e, StatusEvent) for e in events)
    assert messages[-1].role == "assistant"
    assert messages[-1].text == "recovered"


@pytest.mark.asyncio
async def test_run_query_ptl_disabled_when_preserve_recent_none() -> None:
    streamer = _AlwaysOverflowStreamer()
    ctx = QueryContext(
        client=streamer,
        tools=_FakeDispatcher(),
        max_turns=1,
        ptl_preserve_recent=None,
    )
    messages = [ConversationMessage(role="user", content=[TextBlock(text="hi")])]
    with pytest.raises(httpx.HTTPStatusError):
        async for _ in run_query(ctx, messages):
            pass


@pytest.mark.asyncio
async def test_run_query_ptl_no_retry_after_second_overflow() -> None:
    streamer = _AlwaysOverflowStreamer()
    ctx = QueryContext(
        client=streamer,
        tools=_FakeDispatcher(),
        max_turns=1,
        ptl_preserve_recent=DEFAULT_KEEP_RECENT,
    )
    messages: list[ConversationMessage] = []
    for i in range(12):
        messages.append(ConversationMessage(role="user", content=[TextBlock(text=f"round {i} " + "y" * 500)]))
        messages.append(ConversationMessage(role="assistant", content=[TextBlock(text=f"reply {i}")]))

    with pytest.raises(httpx.HTTPStatusError):
        async for _ in run_query(ctx, messages):
            pass


@pytest.mark.asyncio
async def test_run_query_ptl_no_retry_mid_stream() -> None:
    ctx = QueryContext(
        client=_MidStreamOverflowStreamer(),
        tools=_FakeDispatcher(),
        max_turns=1,
        ptl_preserve_recent=DEFAULT_KEEP_RECENT,
    )
    messages = [ConversationMessage(role="user", content=[TextBlock(text="hi")])]
    with pytest.raises(httpx.HTTPStatusError):
        async for _ in run_query(ctx, messages):
            pass
