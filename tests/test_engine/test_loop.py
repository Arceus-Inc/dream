"""Spec 03 stage 2 — ``QueryContext`` + ``run_query`` act-loop.

``run_query`` is the streaming act-loop the turn FSM's ``act`` sub-state
delegates to. It:

- streams a model turn (yielding ``AssistantTextDelta`` + a final
  ``AssistantTurnComplete``);
- appends the assistant message to the transcript;
- dispatches every ``ToolUseBlock`` in order, yielding
  ``ToolExecutionStarted`` / ``ToolExecutionCompleted`` per tool;
- appends *one* user message containing all matching ``ToolResultBlock``s
  (the atom — Spec 00 #1);
- re-enters the model until the assistant turn carries no tool_uses, or
  ``max_turns`` is hit.

The loop's lifecycle decisions are driven by event types and block types,
**never** by parsing assistant prose (Spec 03 acceptance #7). The loop is
bounded by ``max_turns`` (acceptance #6).

Tests use small in-test fakes (``FakeStreamer``, ``FakeDispatcher``) so the
behaviour is pinned against a deterministic provider rather than over the
real cross-repo Protocol.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from dream.engine._cost import UsageSnapshot
from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from dream.engine._loop import QueryContext, run_query
from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

# --- in-test fakes ------------------------------------------------------------


@dataclass
class FakeTurn:
    """One scripted model turn for the fake streamer."""

    text_chunks: list[str] = field(default_factory=list)
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)


class FakeStreamer:
    """Yields scripted ``StreamEvent``s for each call to ``stream_turn``.

    Records a snapshot of the messages passed on every call so tests can
    assert what the loop sent back to the model on re-entry.
    """

    def __init__(self, turns: list[FakeTurn]) -> None:
        self._remaining = list(turns)
        self.calls: list[list[ConversationMessage]] = []

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append([m for m in messages])
        if not self._remaining:
            raise AssertionError(
                "FakeStreamer ran out of scripted turns — loop kept re-entering"
            )
        turn = self._remaining.pop(0)
        for chunk in turn.text_chunks:
            yield AssistantTextDelta(text=chunk)
        blocks: list[ContentBlock] = []
        joined = "".join(turn.text_chunks)
        if joined:
            blocks.append(TextBlock(text=joined))
        blocks.extend(turn.tool_uses)
        yield AssistantTurnComplete(blocks=blocks, usage=turn.usage)


class FakeDispatcher:
    """Returns scripted (content, is_error) per tool name; defaults to ok."""

    def __init__(
        self,
        results: dict[str, tuple[str, bool]] | None = None,
        *,
        raise_for: dict[str, Exception] | None = None,
    ) -> None:
        self.results = results or {}
        self.raise_for = raise_for or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def dispatch(self, name: str, input: dict[str, Any]) -> tuple[str, bool]:
        self.calls.append((name, dict(input)))
        if name in self.raise_for:
            raise self.raise_for[name]
        return self.results.get(name, (f"ok:{name}", False))


async def _drain(
    ctx: QueryContext, messages: list[ConversationMessage]
) -> list[StreamEvent]:
    out: list[StreamEvent] = []
    async for ev in run_query(ctx, messages):
        out.append(ev)
    return out


# --- QueryContext shape -------------------------------------------------------


def test_query_context_construction_takes_client_tools_and_max_turns() -> None:
    ctx = QueryContext(
        client=FakeStreamer(turns=[]),
        tools=FakeDispatcher(),
        max_turns=8,
    )
    assert ctx.max_turns == 8
    assert ctx.client is not None
    assert ctx.tools is not None


# --- run_query: single text turn ---------------------------------------------


async def test_run_query_text_only_turn_ends_loop() -> None:
    client = FakeStreamer(
        turns=[FakeTurn(text_chunks=["hello "], usage=UsageSnapshot(input_tokens=3, output_tokens=1))]
    )
    ctx = QueryContext(client=client, tools=FakeDispatcher(), max_turns=8)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="hi")])
    ]
    events = await _drain(ctx, messages)
    # AssistantTextDelta + AssistantTurnComplete, in that order, exactly one of each
    assert [type(e).__name__ for e in events] == ["AssistantTextDelta", "AssistantTurnComplete"]
    # Loop appended one assistant message, no tool round
    assert len(messages) == 2
    assert messages[-1].role == "assistant"
    assert messages[-1].text == "hello "
    # Streamer was called exactly once — no re-entry
    assert len(client.calls) == 1


async def test_run_query_appends_assistant_message_with_full_block_content() -> None:
    """The assistant message that lands in the transcript has the full blocks the model returned."""
    tool_use = ToolUseBlock(id="t1", name="noop", input={})
    client = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["thinking"], tool_uses=[tool_use]),
            FakeTurn(text_chunks=["done"]),
        ]
    )
    ctx = QueryContext(client=client, tools=FakeDispatcher(), max_turns=8)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="go")])
    ]
    await _drain(ctx, messages)
    first_assistant = messages[1]
    assert first_assistant.role == "assistant"
    assert first_assistant.tool_uses == [tool_use]
    assert first_assistant.text == "thinking"


# --- run_query: tool round ---------------------------------------------------


async def test_run_query_dispatches_tool_appends_result_and_reenters() -> None:
    """The full canonical tool round, observable end-to-end."""
    tool_use = ToolUseBlock(id="t1", name="read", input={"path": "/a"})
    client = FakeStreamer(
        turns=[
            FakeTurn(tool_uses=[tool_use], usage=UsageSnapshot(input_tokens=10, output_tokens=4)),
            FakeTurn(text_chunks=["here's the file"], usage=UsageSnapshot(input_tokens=15, output_tokens=5)),
        ]
    )
    tools = FakeDispatcher(results={"read": ("FILE CONTENTS", False)})
    ctx = QueryContext(client=client, tools=tools, max_turns=8)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="read /a")])
    ]
    events = await _drain(ctx, messages)

    # Event order: tool turn complete → started → completed → text delta → final complete
    type_seq = [type(e).__name__ for e in events]
    assert type_seq == [
        "AssistantTurnComplete",         # first turn (tool_use only, no text deltas)
        "ToolExecutionStarted",
        "ToolExecutionCompleted",
        "AssistantTextDelta",            # second turn
        "AssistantTurnComplete",
    ]

    # Tool was called with the right args
    assert tools.calls == [("read", {"path": "/a"})]

    # Transcript: user → assistant(tool_use) → user(tool_result) → assistant(text)
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[1].tool_uses == [tool_use]
    assert messages[2].tool_results == [
        ToolResultBlock(tool_use_id="t1", content="FILE CONTENTS", is_error=False)
    ]
    assert messages[3].text == "here's the file"

    # Streamer was re-entered exactly once
    assert len(client.calls) == 2


async def test_run_query_parallel_tools_dispatched_in_order_in_one_user_message() -> None:
    """N tool_uses in one assistant turn → N ToolResultBlocks in ONE user message (atom)."""
    tu1 = ToolUseBlock(id="t1", name="read", input={"path": "/a"})
    tu2 = ToolUseBlock(id="t2", name="read", input={"path": "/b"})
    client = FakeStreamer(
        turns=[
            FakeTurn(tool_uses=[tu1, tu2]),
            FakeTurn(text_chunks=["ok"]),
        ]
    )
    tools = FakeDispatcher(
        results={"read": ("R", False)}
    )
    ctx = QueryContext(client=client, tools=tools, max_turns=8)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="read both")])
    ]
    events = await _drain(ctx, messages)

    started = [e for e in events if isinstance(e, ToolExecutionStarted)]
    completed = [e for e in events if isinstance(e, ToolExecutionCompleted)]
    assert [e.id for e in started] == ["t1", "t2"]
    assert [e.id for e in completed] == ["t1", "t2"]

    # Both results bundled into a single user message — never split
    user_results_msgs = [m for m in messages if m.role == "user" and m.tool_results]
    assert len(user_results_msgs) == 1
    assert [r.tool_use_id for r in user_results_msgs[0].tool_results] == ["t1", "t2"]


async def test_run_query_tool_failure_marks_is_error_and_continues() -> None:
    """A tool returning ``is_error=True`` does NOT end the loop — model gets the error and continues."""
    tu = ToolUseBlock(id="t1", name="bad", input={})
    client = FakeStreamer(
        turns=[
            FakeTurn(tool_uses=[tu]),
            FakeTurn(text_chunks=["sorry, retrying"]),
        ]
    )
    tools = FakeDispatcher(results={"bad": ("permission denied", True)})
    ctx = QueryContext(client=client, tools=tools, max_turns=8)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="do bad thing")])
    ]
    events = await _drain(ctx, messages)

    completed = next(e for e in events if isinstance(e, ToolExecutionCompleted))
    assert completed.is_error is True
    assert completed.result == "permission denied"

    tr = messages[2].tool_results[0]
    assert tr.is_error is True
    assert tr.content == "permission denied"

    # Loop continued and finished cleanly
    assert messages[-1].role == "assistant"
    assert messages[-1].text == "sorry, retrying"


async def test_run_query_tool_exception_becomes_is_error_event_and_does_not_crash() -> None:
    """An exception raised by the dispatcher is caught and surfaced as an error result."""
    tu = ToolUseBlock(id="t1", name="explode", input={})
    client = FakeStreamer(
        turns=[
            FakeTurn(tool_uses=[tu]),
            FakeTurn(text_chunks=["recovered"]),
        ]
    )
    tools = FakeDispatcher(raise_for={"explode": RuntimeError("kaboom")})
    ctx = QueryContext(client=client, tools=tools, max_turns=8)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="try it")])
    ]
    events = await _drain(ctx, messages)

    completed = next(e for e in events if isinstance(e, ToolExecutionCompleted))
    assert completed.is_error is True
    assert "kaboom" in completed.result
    assert messages[-1].text == "recovered"


# --- run_query: bounded by max_turns -----------------------------------------


async def test_run_query_bounded_by_max_turns_when_model_keeps_calling_tools() -> None:
    """Model that keeps requesting tools → loop stops at ``max_turns`` entries."""
    # Script enough turns to exceed max_turns; each requests one tool.
    turns = [FakeTurn(tool_uses=[ToolUseBlock(id=f"t{i}", name="loop", input={})]) for i in range(20)]
    client = FakeStreamer(turns=turns)
    tools = FakeDispatcher()
    ctx = QueryContext(client=client, tools=tools, max_turns=3)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="loop")])
    ]
    await _drain(ctx, messages)

    # Streamer was called exactly max_turns times — never more.
    assert len(client.calls) == 3
    # Tools were dispatched once per turn — three times.
    assert len(tools.calls) == 3


async def test_run_query_max_turns_one_runs_single_turn() -> None:
    client = FakeStreamer(turns=[FakeTurn(text_chunks=["done"])])
    ctx = QueryContext(client=client, tools=FakeDispatcher(), max_turns=1)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="hi")])
    ]
    await _drain(ctx, messages)
    assert len(client.calls) == 1


# --- run_query: lifecycle never decided by prose -----------------------------


async def test_run_query_prose_saying_done_does_not_end_a_turn_with_tool_uses() -> None:
    """Even if the model writes 'i'm done' it must still get re-entered when tool_uses are present."""
    tu = ToolUseBlock(id="t1", name="noop", input={})
    client = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["i'm done now"], tool_uses=[tu]),
            FakeTurn(text_chunks=["actually here"]),
        ]
    )
    ctx = QueryContext(client=client, tools=FakeDispatcher(), max_turns=8)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="go")])
    ]
    await _drain(ctx, messages)
    assert len(client.calls) == 2  # re-entered despite "i'm done" prose
    assert messages[-1].text == "actually here"


async def test_run_query_text_with_no_tool_uses_ends_loop_regardless_of_prose() -> None:
    """No tool_uses → loop ends, even when prose says 'one more tool please'."""
    client = FakeStreamer(
        turns=[FakeTurn(text_chunks=["one more tool please"])]
    )
    ctx = QueryContext(client=client, tools=FakeDispatcher(), max_turns=8)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="go")])
    ]
    await _drain(ctx, messages)
    assert len(client.calls) == 1


# --- run_query: usage propagation --------------------------------------------


async def test_run_query_assistant_turn_complete_events_expose_usage_per_turn() -> None:
    """Each AssistantTurnComplete carries that turn's UsageSnapshot — the input the CostTracker consumes."""
    client = FakeStreamer(
        turns=[
            FakeTurn(
                tool_uses=[ToolUseBlock(id="t1", name="noop", input={})],
                usage=UsageSnapshot(input_tokens=10, output_tokens=4),
            ),
            FakeTurn(
                text_chunks=["done"],
                usage=UsageSnapshot(input_tokens=20, output_tokens=8),
            ),
        ]
    )
    ctx = QueryContext(client=client, tools=FakeDispatcher(), max_turns=8)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text="x")])
    ]
    events = await _drain(ctx, messages)

    completes = [e for e in events if isinstance(e, AssistantTurnComplete)]
    assert [e.usage for e in completes] == [
        UsageSnapshot(input_tokens=10, output_tokens=4),
        UsageSnapshot(input_tokens=20, output_tokens=8),
    ]


# --- run_query: ErrorEvent is reachable but not synthesised here ------------


def test_error_event_is_a_valid_stream_event() -> None:
    """Sanity: callers can construct ErrorEvent and pass it through the union."""
    ev: StreamEvent = ErrorEvent(message="x")
    assert isinstance(ev, ErrorEvent)
