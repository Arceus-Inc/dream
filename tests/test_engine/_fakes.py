"""Test-only fakes for the engine's act-loop / session orchestrator.

This module is intentionally underscore-prefixed so pytest skips it
during collection; it's imported by ``test_session.py`` (and could be
imported by ``test_loop.py`` later if it ever wants to dedupe).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from dream.engine._cost import UsageSnapshot
from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StreamEvent,
)
from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)


@dataclass
class FakeTurn:
    """One scripted model turn.

    ``delay`` lets a test simulate a slow turn that exceeds
    ``turn_timeout_seconds`` — the streamer awaits sleep(delay) *before*
    yielding so the cancellation lands while the turn is in flight.
    """

    text_chunks: list[str] = field(default_factory=list)
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)
    delay: float = 0.0


class FakeStreamer:
    """Yields scripted ``StreamEvent``s per call; records each call's messages."""

    def __init__(self, turns: list[FakeTurn]) -> None:
        self._remaining = list(turns)
        self.calls: list[list[ConversationMessage]] = []

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append([m for m in messages])
        if not self._remaining:
            raise AssertionError(
                "FakeStreamer ran out of scripted turns — the loop re-entered "
                "more times than expected"
            )
        turn = self._remaining.pop(0)
        if turn.delay > 0:
            await asyncio.sleep(turn.delay)
        for chunk in turn.text_chunks:
            yield AssistantTextDelta(text=chunk)
        blocks: list[ContentBlock] = []
        joined = "".join(turn.text_chunks)
        if joined:
            blocks.append(TextBlock(text=joined))
        blocks.extend(turn.tool_uses)
        yield AssistantTurnComplete(blocks=blocks, usage=turn.usage)


class FakeDispatcher:
    """Records every dispatch; returns scripted (content, is_error) per tool name."""

    def __init__(
        self,
        results: dict[str, tuple[str, bool]] | None = None,
    ) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def dispatch(self, name: str, input: dict[str, Any]) -> tuple[str, bool]:
        self.calls.append((name, dict(input)))
        return self.results.get(name, (f"ok:{name}", False))
