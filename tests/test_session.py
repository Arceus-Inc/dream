"""Spec 05 slice D -- public ``Session.send`` against a ``QueryEngine``.

These tests pin the behaviour the demo (REPL upgrade #2) and external
SDK consumers will rely on:

- ``send(prompt)`` returns an async iterator of public ``events.Event``
  values; internal ``StreamEvent`` / orchestration events stay private.
- Each ``AssistantTurnComplete`` maps to one public ``TurnComplete``
  whose ``usage`` dict carries the per-turn token counters.
- ``send`` accumulates assistant + tool-result messages back into the
  session transcript so the *next* ``send`` continues the same thread
  rather than restarting orientation or losing tool context.
- ``Session.cost`` is the running total of every ``UsageSnapshot``.
- ``cancel`` closes the in-flight inner generator; ``close`` marks the
  session closed and refuses subsequent ``send`` calls.

The tests deliberately drive ``Session`` with the in-repo ``FakeStreamer``
+ ``FakeDispatcher`` fakes so the contract is verified independently of
any provider adapter. The Spec 02 adapter wiring lands in REPL upgrade #2.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest

from dream.engine._cost import UsageSnapshot
from dream.engine._engine import QueryEngine
from dream.engine._messages import ToolUseBlock
from dream.events import (
    Event,
    TextDelta,
    ToolUseResult,
    ToolUseStart,
    TurnComplete,
)
from dream.session import Session, SessionOptions
from dream.subagents._async_delegation import AsyncDelegationManager
from dream.subagents._projection import SubagentResult
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn


def _engine(streamer: FakeStreamer, dispatcher: FakeDispatcher) -> QueryEngine:
    return QueryEngine(
        streamer=streamer,
        dispatcher=dispatcher,
        session_id="s_test",
        working_dir=Path("/tmp"),
        max_turns=4,
    )


async def _collect(session: Session, prompt: str) -> list[Event]:
    out: list[Event] = []
    async for ev in session.send(prompt):
        out.append(ev)
    return out


# --- basic send: text only ---------------------------------------------------


async def test_session_send_text_only_yields_textdelta_then_turncomplete() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(
                text_chunks=["hello ", "world"],
                usage=UsageSnapshot(input_tokens=5, output_tokens=2),
            )
        ]
    )
    dispatcher = FakeDispatcher()
    session = Session(id="s1", _engine=_engine(streamer, dispatcher))

    events = await _collect(session, "hi")

    text_chunks = [e.text for e in events if isinstance(e, TextDelta)]
    assert text_chunks == ["hello ", "world"]

    turn_completes = [e for e in events if isinstance(e, TurnComplete)]
    assert len(turn_completes) == 1
    assert turn_completes[0].usage["input_tokens"] == 5
    assert turn_completes[0].usage["output_tokens"] == 2

    # No tool events when no tool_uses were emitted.
    assert not any(isinstance(e, ToolUseStart | ToolUseResult) for e in events)


async def test_session_send_passes_prompt_to_streamer() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])
    session = Session(id="s1", _engine=_engine(streamer, FakeDispatcher()))

    await _collect(session, "summon the moon")

    assert len(streamer.calls) == 1
    first_call = streamer.calls[0]
    # ``run_session`` may prepend (orientation) but the user prompt is the
    # last message in the streamer's view on the first turn.
    last = first_call[-1]
    assert last.role == "user"
    assert "summon the moon" in last.text


# --- tool dispatch path ------------------------------------------------------


async def test_session_send_with_tool_yields_tool_events_and_two_turncompletes() -> None:
    tool_use = ToolUseBlock(id="tu_1", name="echo", input={"x": 1})
    streamer = FakeStreamer(
        turns=[
            FakeTurn(
                text_chunks=["calling tool"],
                tool_uses=[tool_use],
                usage=UsageSnapshot(input_tokens=3, output_tokens=1),
            ),
            FakeTurn(
                text_chunks=["done"],
                usage=UsageSnapshot(input_tokens=4, output_tokens=2),
            ),
        ]
    )
    dispatcher = FakeDispatcher(results={"echo": ("result-content", False)})
    session = Session(id="s1", _engine=_engine(streamer, dispatcher))

    events = await _collect(session, "use tool")

    starts = [e for e in events if isinstance(e, ToolUseStart)]
    results = [e for e in events if isinstance(e, ToolUseResult)]
    completes = [e for e in events if isinstance(e, TurnComplete)]

    assert len(starts) == 1
    assert starts[0].name == "echo"
    assert starts[0].tool_use_id == "tu_1"
    assert starts[0].input == {"x": 1}

    assert len(results) == 1
    assert results[0].name == "echo"
    assert results[0].tool_use_id == "tu_1"
    assert results[0].content == "result-content"
    assert results[0].is_error is False

    # Two AssistantTurnComplete events -> two public TurnComplete events.
    assert len(completes) == 2

    # Order: text deltas, tool start, tool result, more deltas, both completes.
    types_in_order = [type(e).__name__ for e in events]
    assert types_in_order.index("ToolUseStart") < types_in_order.index("ToolUseResult")
    # The first turn's TurnComplete must precede the *second* turn's text
    # output ("done"). Comparing the two TurnComplete indices is tautological
    # (the second is found *after* the first by construction); pinning the
    # first completion before the next turn's text proves real interleaving
    # of turn boundaries with streamed output (#31).
    first_complete_idx = types_in_order.index("TurnComplete")
    second_turn_text_idx = next(
        i for i, e in enumerate(events) if isinstance(e, TextDelta) and e.text == "done"
    )
    assert first_complete_idx < second_turn_text_idx


async def test_session_send_propagates_tool_error_flag() -> None:
    tool_use = ToolUseBlock(id="tu_e", name="bad", input={})
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=[""], tool_uses=[tool_use]),
            FakeTurn(text_chunks=["ack"]),
        ]
    )
    dispatcher = FakeDispatcher(results={"bad": ("boom", True)})
    session = Session(id="s1", _engine=_engine(streamer, dispatcher))

    events = await _collect(session, "go")

    [result_ev] = [e for e in events if isinstance(e, ToolUseResult)]
    assert result_ev.is_error is True
    assert result_ev.content == "boom"


# --- transcript persistence across sends ------------------------------------


async def test_session_transcript_persists_across_sends() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["a"], usage=UsageSnapshot(input_tokens=1)),
            FakeTurn(text_chunks=["b"], usage=UsageSnapshot(input_tokens=2)),
        ]
    )
    session = Session(id="s1", _engine=_engine(streamer, FakeDispatcher()))

    await _collect(session, "prompt-1")
    await _collect(session, "prompt-2")

    # The streamer saw two turn calls. The second call must include the
    # full history of the first (user msg + assistant reply) plus the new
    # user message -- otherwise the conversation has no memory.
    assert len(streamer.calls) == 2
    second_call_msgs = streamer.calls[1]
    roles = [m.role for m in second_call_msgs]
    # At minimum: user (prompt-1), assistant (reply-1), user (prompt-2).
    assert roles[-3:] == ["user", "assistant", "user"]
    assert second_call_msgs[-1].text == "prompt-2"
    assert second_call_msgs[-3].text == "prompt-1"
    assert second_call_msgs[-2].text == "a"


async def test_session_transcript_persists_through_tool_use_across_sends() -> None:
    tool_use = ToolUseBlock(id="tu_1", name="echo", input={})
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["calling"], tool_uses=[tool_use]),
            FakeTurn(text_chunks=["wrap-1"]),
            FakeTurn(text_chunks=["wrap-2"]),
        ]
    )
    dispatcher = FakeDispatcher(results={"echo": ("done", False)})
    session = Session(id="s1", _engine=_engine(streamer, dispatcher))

    await _collect(session, "p1")
    await _collect(session, "p2")

    # Third call must carry the tool_use + tool_result atom from the prior
    # send. Otherwise the model sees a dangling tool_use and rejects.
    third_call = streamer.calls[2]
    # Walk from the end: last is the new user prompt, before that the
    # prior assistant wrap message, before that a user-tool-result
    # message, before that the assistant-tool-use message.
    assert third_call[-1].text == "p2"
    assert third_call[-2].text == "wrap-1"
    # The tool result message: a user message with one ToolResultBlock.
    tool_result_msg = third_call[-3]
    assert tool_result_msg.role == "user"
    assert len(tool_result_msg.tool_results) == 1
    assert tool_result_msg.tool_results[0].tool_use_id == "tu_1"
    # The matching assistant tool_use message must also survive into the
    # resumed history -- a tool_result with no preceding tool_use is a
    # dangling atom the model rejects. Checking only the result (#32) misses
    # the case where the assistant tool_use block was dropped on flush.
    tool_use_msg = third_call[-4]
    assert tool_use_msg.role == "assistant"
    assert len(tool_use_msg.tool_uses) == 1
    assert tool_use_msg.tool_uses[0].id == "tu_1"


# --- cost accumulation -------------------------------------------------------


async def test_session_cost_accumulates_across_turns_and_sends() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(
                text_chunks=["x"],
                usage=UsageSnapshot(input_tokens=5, output_tokens=1),
            ),
            FakeTurn(
                text_chunks=["y"],
                usage=UsageSnapshot(input_tokens=7, output_tokens=2),
            ),
        ]
    )
    session = Session(id="s1", _engine=_engine(streamer, FakeDispatcher()))

    await _collect(session, "p1")
    await _collect(session, "p2")

    assert session.cost.input_tokens == 12
    assert session.cost.output_tokens == 3


# --- cancel ------------------------------------------------------------------


async def test_session_cancel_closes_inflight_stream() -> None:
    # A slow turn so we can race cancel against it.
    streamer = FakeStreamer(
        turns=[FakeTurn(text_chunks=["never"], delay=1.0)],
    )
    session = Session(id="s1", _engine=_engine(streamer, FakeDispatcher()))

    events: list[Event] = []

    async def _drive() -> None:
        async for ev in session.send("slow"):
            events.append(ev)

    drive_task = asyncio.create_task(_drive())
    # Yield once so the inner generator actually starts.
    await asyncio.sleep(0.05)
    await session.cancel()
    # The driver task should finish cleanly (no events yielded).
    await asyncio.wait_for(drive_task, timeout=2.0)

    assert events == []


# --- close -------------------------------------------------------------------


async def test_session_close_marks_session_closed() -> None:
    session = Session(
        id="s1",
        _engine=_engine(FakeStreamer(turns=[]), FakeDispatcher()),
    )

    await session.close()

    with pytest.raises(RuntimeError, match="closed"):
        async for _ in session.send("after close"):
            break


async def test_session_close_is_idempotent() -> None:
    session = Session(
        id="s1",
        _engine=_engine(FakeStreamer(turns=[]), FakeDispatcher()),
    )

    await session.close()
    await session.close()  # second call must not raise


# --- no-engine fallback (preserves existing placeholder behaviour) ----------


async def test_session_send_without_engine_raises_not_implemented() -> None:
    session = Session(id="s1", options=SessionOptions())

    with pytest.raises(NotImplementedError):
        async for _ in session.send("x"):
            break


async def test_session_cancel_without_engine_is_noop() -> None:
    """``cancel`` on a fresh session with no in-flight task must not raise.

    Background apps may call ``cancel`` defensively in finally blocks
    before any ``send`` has been issued.
    """
    session = Session(id="s1")
    await session.cancel()  # must not raise


async def test_session_cancel_interrupts_owned_background_children() -> None:
    manager = AsyncDelegationManager(max_active=1)
    cancelled = asyncio.Event()

    async def work() -> tuple[SubagentResult, ...]:
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    assert manager.start("s1", ("reviewer",), work) is not None
    await asyncio.sleep(0)
    engine = _engine(FakeStreamer([]), FakeDispatcher())
    engine.delegations = manager
    session = Session(id="s1", _engine=engine)

    await session.cancel()

    assert cancelled.is_set()
    assert manager.active("s1") == 0
    await manager.close()


# --- public surface ----------------------------------------------------------


def test_session_id_and_options_remain_public() -> None:
    opts = SessionOptions(model="gpt-x", max_turns=2)
    session = Session(
        id="abc",
        options=opts,
        _engine=_engine(FakeStreamer(turns=[]), FakeDispatcher()),
    )

    assert session.id == "abc"
    assert session.options is opts


def test_session_engine_kwarg_is_underscore_prefixed_internal() -> None:
    """The engine binding kwarg is intentionally underscore-prefixed.

    Public users construct ``Session`` via ``Harness.start_session``; the
    underscore signals the kwarg is harness-internal and may change.
    """
    import inspect

    sig = inspect.signature(Session.__init__)
    assert "_engine" in sig.parameters
    p = sig.parameters["_engine"]
    assert p.default is None
    # Keyword-only -- positional binding would be a silent API leak.
    assert p.kind is inspect.Parameter.KEYWORD_ONLY


# --- usage payload shape -----------------------------------------------------


async def test_turn_complete_usage_carries_all_token_counters() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(
                text_chunks=["ok"],
                usage=UsageSnapshot(
                    input_tokens=10,
                    output_tokens=20,
                    cache_read_tokens=30,
                    cache_write_tokens=40,
                ),
            ),
        ]
    )
    session = Session(id="s1", _engine=_engine(streamer, FakeDispatcher()))

    events = await _collect(session, "p")

    [complete] = [e for e in events if isinstance(e, TurnComplete)]
    assert complete.usage == {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 30,
        "cache_write_tokens": 40,
    }
    assert complete.stop_reason in {"end_turn", "tool_use"}


def test_session_cost_starts_zeroed() -> None:
    session = Session(id="s1")
    assert session.cost.input_tokens == 0
    assert session.cost.output_tokens == 0
    assert session.cost.cache_read_tokens == 0
    assert session.cost.cache_write_tokens == 0


# --- helper: ensure ToolUseStart input is a copy, not a live reference ------


async def test_tool_use_start_input_is_independent_of_dispatcher_mutation() -> None:
    tool_use = ToolUseBlock(id="tu_1", name="echo", input={"k": "v"})
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["go"], tool_uses=[tool_use]),
            FakeTurn(text_chunks=["done"]),
        ]
    )

    class _MutatingDispatcher:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def dispatch(self, name: str, input: dict[str, Any]) -> tuple[str, bool]:
            self.calls.append(input)
            input["k"] = "mutated"
            return ("ok", False)

    session = Session(
        id="s1",
        _engine=QueryEngine(
            streamer=streamer,
            dispatcher=_MutatingDispatcher(),  # type: ignore[arg-type]
            session_id="s",
            working_dir=Path("/tmp"),
        ),
    )

    events = await _collect(session, "p")

    [start_ev] = [e for e in events if isinstance(e, ToolUseStart)]
    # The event payload must not be affected by the dispatcher's later
    # mutation -- the engine already deep-copies on emit.
    assert start_ev.input == {"k": "v"}


# --- compaction translation (slice E) ---------------------------------------


def test_translate_compaction_done_event_yields_public_compacted() -> None:
    """``Session._translate`` maps the internal ``CompactionDoneEvent`` to
    the public ``Compacted`` value, carrying through the removed-message
    count and freed-token estimate.
    """
    from dream.engine._events import CompactionDoneEvent
    from dream.events import Compacted

    session = Session(id="s_translate")
    pending: list = []
    ev = CompactionDoneEvent(
        tier="microcompact",
        removed_messages=3,
        freed_tokens=480,
        resulting_utilisation=0.42,
    )
    out = session._translate(ev, pending)
    assert isinstance(out, Compacted)
    assert out.removed_messages == 3
    assert out.summary_tokens == 480


# --- CodeAnt #35: error tool results get a generic transcript marker --------


async def test_session_tool_error_result_uses_generic_transcript_marker() -> None:
    """The public ``ToolUseResult`` event keeps the detailed error content
    (observability), but the transcript the model re-reads on resume must
    only carry the engine's generic, non-revealing marker (#35).
    """
    from dream.engine._messages import ToolResultBlock as _TRB

    tool_use = ToolUseBlock(id="tu_e", name="bad", input={})
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=[""], tool_uses=[tool_use]),
            FakeTurn(text_chunks=["ack"]),
        ]
    )
    # The dispatcher returns a detailed, internal-looking error payload.
    leaky = "RuntimeError: secret /etc/shadow not readable at line 42"
    dispatcher = FakeDispatcher(results={"bad": (leaky, True)})
    session = Session(id="s1", _engine=_engine(streamer, dispatcher))

    events = await _collect(session, "go")

    # Public event keeps the detail.
    [result_ev] = [e for e in events if isinstance(e, ToolUseResult)]
    assert result_ev.is_error is True
    assert result_ev.content == leaky

    # Transcript ToolResultBlock must NOT carry the detail -- it carries the
    # generic marker matching ``run_query``'s contract.
    blocks = [
        block
        for msg in session._transcript
        for block in msg.content
        if isinstance(block, _TRB)
    ]
    assert len(blocks) == 1
    assert blocks[0].content == "tool 'bad' failed to execute"
    assert leaky not in blocks[0].content


# --- CodeAnt #33: single-flight guard on concurrent send --------------------


async def test_session_rejects_concurrent_send() -> None:
    """A second ``send`` while one is in flight must be rejected so the two
    calls don't clobber each other's cancel state (#33).
    """
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["slow"], delay=0.5)])
    session = Session(id="s1", _engine=_engine(streamer, FakeDispatcher()))

    first = session.send("first")
    # Start the first stream so ``_active`` is set.
    started = asyncio.create_task(first.__anext__())
    await asyncio.sleep(0.05)

    with pytest.raises(RuntimeError, match="already in flight"):
        async for _ in session.send("second"):
            break

    await session.cancel()
    with contextlib.suppress(BaseException):
        await started


# --- CodeAnt #34: pending tool results flushed even on the cancel path -------


async def test_session_flushes_pending_tool_results_on_cancel() -> None:
    """If a send is cancelled after a tool completed but before the next
    assistant turn, the buffered tool result must still be committed to the
    transcript -- otherwise the prior assistant tool_use dangles (#34).
    """
    from dream.engine._messages import ToolResultBlock as _TRB

    tool_use = ToolUseBlock(id="tu_1", name="echo", input={})
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["calling"], tool_uses=[tool_use]),
            # Second turn is slow so we can cancel after the tool result
            # event but before this turn completes.
            FakeTurn(text_chunks=["never"], delay=1.0),
        ]
    )
    dispatcher = FakeDispatcher(results={"echo": ("done", False)})
    session = Session(id="s1", _engine=_engine(streamer, dispatcher))

    seen: list[Event] = []

    async def _drive() -> None:
        async for ev in session.send("go"):
            seen.append(ev)
            if isinstance(ev, ToolUseResult):
                await session.cancel()

    await asyncio.wait_for(_drive(), timeout=3.0)

    # The tool result was buffered (we saw the public event) and must now be
    # in the transcript despite the cancel.
    assert any(isinstance(e, ToolUseResult) for e in seen)
    result_blocks = [
        block
        for msg in session._transcript
        for block in msg.content
        if isinstance(block, _TRB)
    ]
    assert len(result_blocks) == 1
    assert result_blocks[0].tool_use_id == "tu_1"


# --- CodeAnt #41: CompactionDoneEvent applies the compacted shape -----------


def test_translate_compaction_applies_compacted_shape_to_transcript() -> None:
    """Handling ``CompactionDoneEvent`` must bring ``_transcript`` to the
    compacted shape so the next ``send`` resumes from compacted, not stale,
    history (#41).
    """
    from dream.contracts.provider import ProviderCapabilities
    from dream.engine._events import CompactionDoneEvent
    from dream.engine._messages import (
        ConversationMessage,
        ToolResultBlock,
    )
    from dream.services.compact import TIME_BASED_MC_CLEARED_MESSAGE
    from dream.services.compact._orchestrator import AutoCompactState

    compactor = AutoCompactState()
    engine = QueryEngine(
        streamer=FakeStreamer(turns=[]),
        dispatcher=FakeDispatcher(),
        session_id="s",
        working_dir=Path("/tmp"),
        compactor=compactor,
        compaction_capabilities=ProviderCapabilities(max_context_tokens=128_000),
    )
    session = Session(id="s1", _engine=engine)

    big_blob = "X" * 4096
    for i in range(8):
        tu_id = f"tu_{i}"
        session._transcript.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=tu_id, name="bash", input={"cmd": "ls"})],
            )
        )
        session._transcript.append(
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id=tu_id, content=big_blob, is_error=False)],
            )
        )

    pre_blobs = sum(
        1
        for msg in session._transcript
        for block in msg.content
        if isinstance(block, ToolResultBlock) and block.content == big_blob
    )
    assert pre_blobs == 8

    ev = CompactionDoneEvent(
        tier="microcompact",
        removed_messages=0,
        freed_tokens=1000,
        resulting_utilisation=0.1,
    )
    session._translate(ev, [])

    post_blobs = sum(
        1
        for msg in session._transcript
        for block in msg.content
        if isinstance(block, ToolResultBlock) and block.content == big_blob
    )
    cleared = sum(
        1
        for msg in session._transcript
        for block in msg.content
        if isinstance(block, ToolResultBlock)
        and block.content == TIME_BASED_MC_CLEARED_MESSAGE
    )
    # Older blobs got replaced with the cleared sentinel; the transcript is
    # now the compacted shape, not the stale full history.
    assert post_blobs < pre_blobs
    assert cleared > 0
