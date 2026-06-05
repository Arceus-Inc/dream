"""Spec 03 stage 3a — ``run_session`` orchestrator end-to-end.

``run_session`` is the outer machine that wraps the inner ``run_query``
act-loop. For each user message it walks a turn through the five
sub-states (``read -> plan -> act -> verify -> record``); for the session as
a whole it walks ``starting -> orienting -> working*N -> sealing ->
done|aborted``.

This stage (3a) tests the *deterministic machinery* — transitions,
records, timeouts, crash resume, hook bus, checkpoint hook. The
LLM-dependent rituals (orientation summary, heartbeat coma, reviewer
loop) are stage 3b and use the same orchestrator with richer config.

Acceptance pinned here:
- #1 session order, no skipping, no re-orient.
- #2 ``session.end`` emitted on every exit path (incl. abort).
- #3 exactly one turn record per turn (incl. timeout).
- #4 checkpoint snapshot per successful turn record.
- #10 crash resume re-enters the model on a pending continuation.
- #14 turn timeout writes a synthetic record; N consecutive aborts session.
- #15 hooks fire on every transition.
- #16 hook handler errors never veto.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dream.engine._cost import UsageSnapshot
from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.engine._records import SessionEnd, TurnRecord
from dream.engine._session import (
    SessionConfig,
    SessionEvent,
    run_session,
)
from dream.engine._transitions import TransitionBus, TransitionEvent
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn

# --- helpers ----------------------------------------------------------------


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _ticking_clock(start_seconds: int = 0, step_seconds: int = 1):
    """Returns a deterministic clock that advances by `step` on every call."""
    base = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)
    counter = [start_seconds]

    def now() -> datetime:
        t = base + timedelta(seconds=counter[0])
        counter[0] += step_seconds
        return t

    return now


def _basic_config(
    streamer: FakeStreamer,
    *,
    tools: FakeDispatcher | None = None,
    turn_timeout_seconds: float | None = None,
    max_consecutive_timeouts: int = 3,
    session_id: str = "s_test",
    checkpoint=None,
) -> SessionConfig:
    return SessionConfig(
        client=streamer,
        tools=tools or FakeDispatcher(),
        max_turns=4,
        turn_timeout_seconds=turn_timeout_seconds,
        max_consecutive_timeouts=max_consecutive_timeouts,
        session_id=session_id,
        checkpoint=checkpoint,
        now=_ticking_clock(),
    )


async def _drain(
    config: SessionConfig,
    user_messages: list[ConversationMessage],
    *,
    transitions: TransitionBus | None = None,
    resume_messages: list[ConversationMessage] | None = None,
) -> list[SessionEvent]:
    events: list[SessionEvent] = []
    async for ev in run_session(
        config,
        user_messages,
        transitions=transitions,
        resume_messages=resume_messages,
    ):
        events.append(ev)
    return events


# --- happy path -------------------------------------------------------------


async def test_run_session_happy_path_runs_one_turn_per_user_message() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["one"], usage=UsageSnapshot(input_tokens=10, output_tokens=2)),
            FakeTurn(text_chunks=["two"], usage=UsageSnapshot(input_tokens=20, output_tokens=4)),
            FakeTurn(text_chunks=["three"], usage=UsageSnapshot(input_tokens=30, output_tokens=6)),
        ]
    )
    config = _basic_config(streamer)
    events = await _drain(config, [_user("m1"), _user("m2"), _user("m3")])

    records = [e for e in events if isinstance(e, TurnRecord)]
    ends = [e for e in events if isinstance(e, SessionEnd)]

    assert len(records) == 3
    assert [r.turn_number for r in records] == [1, 2, 3]
    assert all(r.outcome == "complete" for r in records)
    assert len(ends) == 1
    assert ends[0].outcome == "done"
    assert ends[0].turns == 3


async def test_run_session_with_no_user_messages_seals_immediately_done() -> None:
    streamer = FakeStreamer(turns=[])
    config = _basic_config(streamer)
    events = await _drain(config, [])
    records = [e for e in events if isinstance(e, TurnRecord)]
    ends = [e for e in events if isinstance(e, SessionEnd)]
    assert records == []
    assert len(ends) == 1
    assert ends[0].outcome == "done"
    assert ends[0].turns == 0
    assert len(streamer.calls) == 0


async def test_run_session_forwards_inner_stream_events() -> None:
    tu = ToolUseBlock(id="t1", name="read", input={"path": "/x"})
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["hi "], tool_uses=[tu]),
            FakeTurn(text_chunks=["done"]),
        ]
    )
    tools = FakeDispatcher(results={"read": ("CONTENT", False)})
    config = _basic_config(streamer, tools=tools)
    events = await _drain(config, [_user("read x")])

    # Stream events from run_query are forwarded verbatim.
    type_names = [type(e).__name__ for e in events]
    assert "AssistantTextDelta" in type_names
    assert "ToolExecutionStarted" in type_names
    assert "ToolExecutionCompleted" in type_names
    assert "AssistantTurnComplete" in type_names


# --- transitions -------------------------------------------------------------


def _names(events: list[SessionEvent]) -> list[str]:
    return [e.name for e in events if isinstance(e, TransitionEvent)]


async def test_run_session_emits_session_transitions_in_order() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["a"]), FakeTurn(text_chunks=["b"])])
    config = _basic_config(streamer)
    events = await _drain(config, [_user("1"), _user("2")])
    session_names = [n for n in _names(events) if n.startswith("session.")]
    # Two turns → one starting->orienting, one orienting->working,
    # one working->working (between turn 1 and turn 2), one working->sealing,
    # one sealing->done.
    assert session_names == [
        "session.starting.to.orienting",
        "session.orienting.to.working",
        "session.working.to.working",
        "session.working.to.sealing",
        "session.sealing.to.done",
    ]


async def test_run_session_emits_turn_transitions_for_each_turn() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["a"])])
    config = _basic_config(streamer)
    events = await _drain(config, [_user("1")])
    turn_names = [n for n in _names(events) if n.startswith("turn.")]
    assert turn_names == [
        "turn.read.to.plan",
        "turn.plan.to.act",
        "turn.act.to.verify",
        "turn.verify.to.record",
    ]


async def test_run_session_fires_transitions_on_provided_bus() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["a"])])
    config = _basic_config(streamer)
    bus = TransitionBus()
    fired: list[str] = []
    bus.register(lambda ev: fired.append(ev.name))
    await _drain(config, [_user("1")], transitions=bus)
    # Bus saw the same transitions that were yielded.
    assert "session.starting.to.orienting" in fired
    assert "turn.read.to.plan" in fired
    assert "session.sealing.to.done" in fired


async def test_run_session_hook_handler_error_does_not_veto_session() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["a"]), FakeTurn(text_chunks=["b"])])
    config = _basic_config(streamer)
    bus = TransitionBus()

    def boom(_ev: TransitionEvent) -> None:
        raise RuntimeError("hook crashed")

    bus.register(boom)
    events = await _drain(config, [_user("1"), _user("2")], transitions=bus)
    records = [e for e in events if isinstance(e, TurnRecord)]
    ends = [e for e in events if isinstance(e, SessionEnd)]
    # Session completed normally despite every transition raising.
    assert len(records) == 2
    assert len(ends) == 1
    assert ends[0].outcome == "done"
    assert bus.failures > 0


# --- per-turn record content -------------------------------------------------


async def test_turn_record_carries_tools_called_in_dispatch_order() -> None:
    tu1 = ToolUseBlock(id="t1", name="read", input={})
    tu2 = ToolUseBlock(id="t2", name="bash", input={})
    streamer = FakeStreamer(
        turns=[
            FakeTurn(tool_uses=[tu1, tu2]),
            FakeTurn(text_chunks=["done"]),
        ]
    )
    config = _basic_config(streamer, tools=FakeDispatcher())
    events = await _drain(config, [_user("go")])
    rec = next(e for e in events if isinstance(e, TurnRecord))
    assert rec.tools_called == ["read", "bash"]


async def test_turn_record_usage_reflects_that_turn_only() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["1"], usage=UsageSnapshot(input_tokens=10, output_tokens=2)),
            FakeTurn(text_chunks=["2"], usage=UsageSnapshot(input_tokens=20, output_tokens=4)),
        ]
    )
    config = _basic_config(streamer)
    events = await _drain(config, [_user("a"), _user("b")])
    records = [e for e in events if isinstance(e, TurnRecord)]
    assert records[0].usage == UsageSnapshot(input_tokens=10, output_tokens=2)
    assert records[1].usage == UsageSnapshot(input_tokens=20, output_tokens=4)


async def test_session_end_total_usage_sums_every_turn() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["1"], usage=UsageSnapshot(input_tokens=10, output_tokens=2)),
            FakeTurn(text_chunks=["2"], usage=UsageSnapshot(input_tokens=20, output_tokens=4)),
            FakeTurn(text_chunks=["3"], usage=UsageSnapshot(input_tokens=30, output_tokens=6)),
        ]
    )
    config = _basic_config(streamer)
    events = await _drain(config, [_user("a"), _user("b"), _user("c")])
    end = next(e for e in events if isinstance(e, SessionEnd))
    assert end.total_usage == UsageSnapshot(input_tokens=60, output_tokens=12)


async def test_turn_record_verification_result_defaults_to_skipped_in_3a() -> None:
    """3a has no verifier; 3b will wire it. Default outcome is 'skipped'."""
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["a"])])
    config = _basic_config(streamer)
    events = await _drain(config, [_user("1")])
    rec = next(e for e in events if isinstance(e, TurnRecord))
    assert rec.verification_result == "skipped"


async def test_turn_record_started_at_precedes_ended_at() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["a"])])
    config = _basic_config(streamer)
    events = await _drain(config, [_user("1")])
    rec = next(e for e in events if isinstance(e, TurnRecord))
    assert rec.started_at < rec.ended_at


# --- checkpoint hook (spec 03 #4) -------------------------------------------


async def test_checkpoint_called_once_per_successful_turn_record() -> None:
    seen: list[int] = []
    streamer = FakeStreamer(
        turns=[FakeTurn(text_chunks=["1"]), FakeTurn(text_chunks=["2"])]
    )
    config = _basic_config(streamer, checkpoint=lambda r: seen.append(r.turn_number))
    await _drain(config, [_user("a"), _user("b")])
    assert seen == [1, 2]


async def test_checkpoint_not_called_on_timeout_outcome() -> None:
    """A timed-out turn writes a record but is NOT a successful turn — no snapshot."""
    seen: list[int] = []
    streamer = FakeStreamer(
        turns=[FakeTurn(text_chunks=["slow"], delay=0.5)]
    )
    config = _basic_config(
        streamer,
        turn_timeout_seconds=0.05,
        checkpoint=lambda r: seen.append(r.turn_number),
    )
    await _drain(config, [_user("a")])
    assert seen == []


# --- timeouts (spec 03 #14) -------------------------------------------------


async def test_single_turn_timeout_writes_synthetic_record_and_session_continues() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["1"]),
            FakeTurn(text_chunks=["slow"], delay=0.5),
            FakeTurn(text_chunks=["3"]),
        ]
    )
    config = _basic_config(streamer, turn_timeout_seconds=0.05)
    events = await _drain(config, [_user("a"), _user("b"), _user("c")])
    records = [e for e in events if isinstance(e, TurnRecord)]
    ends = [e for e in events if isinstance(e, SessionEnd)]

    assert len(records) == 3
    assert [r.outcome for r in records] == ["complete", "timeout", "complete"]
    assert len(ends) == 1
    assert ends[0].outcome == "done"


async def test_three_consecutive_turn_timeouts_abort_session() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["x"], delay=0.5),
            FakeTurn(text_chunks=["x"], delay=0.5),
            FakeTurn(text_chunks=["x"], delay=0.5),
        ]
    )
    config = _basic_config(
        streamer, turn_timeout_seconds=0.05, max_consecutive_timeouts=3
    )
    events = await _drain(config, [_user("a"), _user("b"), _user("c"), _user("d")])
    records = [e for e in events if isinstance(e, TurnRecord)]
    end = next(e for e in events if isinstance(e, SessionEnd))

    # Exactly three turn attempts — session aborts BEFORE running the 4th.
    assert len(records) == 3
    assert all(r.outcome == "timeout" for r in records)
    assert end.outcome == "aborted"
    assert end.reason == "repeated-timeout"
    # The 4th user message is never sent.
    assert len(streamer.calls) == 3


async def test_successful_turn_resets_consecutive_timeout_counter() -> None:
    """Turns 1+3+5 timeout, turns 2+4 succeed → counter never reaches 3."""
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["slow"], delay=0.5),  # 1: timeout (counter=1)
            FakeTurn(text_chunks=["ok"]),               # 2: complete (counter=0)
            FakeTurn(text_chunks=["slow"], delay=0.5),  # 3: timeout (counter=1)
            FakeTurn(text_chunks=["ok"]),               # 4: complete (counter=0)
            FakeTurn(text_chunks=["slow"], delay=0.5),  # 5: timeout (counter=1)
        ]
    )
    config = _basic_config(
        streamer, turn_timeout_seconds=0.05, max_consecutive_timeouts=3
    )
    events = await _drain(
        config, [_user("a"), _user("b"), _user("c"), _user("d"), _user("e")]
    )
    end = next(e for e in events if isinstance(e, SessionEnd))
    # No abort: every successful turn reset the counter.
    assert end.outcome == "done"
    records = [e for e in events if isinstance(e, TurnRecord)]
    assert [r.outcome for r in records] == [
        "timeout",
        "complete",
        "timeout",
        "complete",
        "timeout",
    ]


# --- crash resume (spec 03 #10) ---------------------------------------------


async def test_resume_with_pending_continuation_reenters_model() -> None:
    """A resumed transcript ending in tool_results owes a model turn — re-enter."""
    resumed = [
        ConversationMessage(role="user", content=[TextBlock(text="read it")]),
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="t1", name="read", input={"path": "/x"})],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="t1", content="contents")],
        ),
    ]
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["final answer"])])
    config = _basic_config(streamer)
    # Crucially: no user message is consumed for the resume — the resume IS the first turn.
    events = await _drain(config, [], resume_messages=resumed)

    # The streamer was called exactly once with the resumed transcript.
    assert len(streamer.calls) == 1
    sent = streamer.calls[0]
    # The pending continuation was preserved (no message dropped).
    assert [m.role for m in sent] == ["user", "assistant", "user"]
    # A turn record was produced for the resumed turn.
    records = [e for e in events if isinstance(e, TurnRecord)]
    assert len(records) == 1
    assert records[0].outcome == "complete"


async def test_resume_sanitises_trailing_dangling_tool_use() -> None:
    """Trailing assistant(tool_use) without tool_result must be trimmed before send."""
    dangling = ConversationMessage(
        role="assistant",
        content=[ToolUseBlock(id="t9", name="read", input={})],
    )
    resumed = [
        ConversationMessage(role="user", content=[TextBlock(text="hi")]),
        dangling,  # malformed tail
    ]
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["sanitised"])])
    config = _basic_config(streamer)
    await _drain(config, [_user("next")], resume_messages=resumed)

    # The first stream_turn call must NOT contain the dangling tool_use.
    first_call = streamer.calls[0]
    for msg in first_call:
        for block in msg.content:
            assert not (
                isinstance(block, ToolUseBlock) and block.id == "t9"
            ), "dangling tool_use leaked through to the provider"


async def test_resume_without_pending_continuation_processes_user_messages_normally() -> None:
    """Resume with a clean transcript → behave exactly like a fresh session w/ that prefix."""
    resumed = [
        ConversationMessage(role="user", content=[TextBlock(text="prior")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="acknowledged")]),
    ]
    streamer = FakeStreamer(
        turns=[FakeTurn(text_chunks=["new"]), FakeTurn(text_chunks=["er"])]
    )
    config = _basic_config(streamer)
    events = await _drain(
        config, [_user("m1"), _user("m2")], resume_messages=resumed
    )
    records = [e for e in events if isinstance(e, TurnRecord)]
    # No extra "resume turn" — exactly one per user message.
    assert len(records) == 2


# --- SessionEnd always emitted (spec 03 #2) ---------------------------------


async def test_session_end_emitted_on_done_path() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["a"])])
    config = _basic_config(streamer)
    events = await _drain(config, [_user("1")])
    assert sum(1 for e in events if isinstance(e, SessionEnd)) == 1


async def test_session_end_emitted_on_abort_path() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["x"], delay=0.5),
            FakeTurn(text_chunks=["x"], delay=0.5),
            FakeTurn(text_chunks=["x"], delay=0.5),
        ]
    )
    config = _basic_config(
        streamer, turn_timeout_seconds=0.05, max_consecutive_timeouts=3
    )
    events = await _drain(config, [_user("a"), _user("b"), _user("c"), _user("d")])
    ends = [e for e in events if isinstance(e, SessionEnd)]
    assert len(ends) == 1
    assert ends[0].outcome == "aborted"


async def test_session_end_is_the_last_event_in_the_stream() -> None:
    """Caller can rely on SessionEnd as a sentinel for end-of-stream."""
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["a"]), FakeTurn(text_chunks=["b"])])
    config = _basic_config(streamer)
    events = await _drain(config, [_user("1"), _user("2")])
    assert isinstance(events[-1], SessionEnd)


# --- exactly one turn record per turn (spec 03 #3) --------------------------


async def test_exactly_one_turn_record_per_turn_on_complete() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["a"]), FakeTurn(text_chunks=["b"])])
    config = _basic_config(streamer)
    events = await _drain(config, [_user("1"), _user("2")])
    records = [e for e in events if isinstance(e, TurnRecord)]
    assert len(records) == 2
    assert {r.turn_number for r in records} == {1, 2}


async def test_exactly_one_turn_record_per_turn_on_timeout() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(text_chunks=["1"]),
            FakeTurn(text_chunks=["slow"], delay=0.5),
        ]
    )
    config = _basic_config(streamer, turn_timeout_seconds=0.05)
    events = await _drain(config, [_user("a"), _user("b")])
    records = [e for e in events if isinstance(e, TurnRecord)]
    assert len(records) == 2
    assert records[1].outcome == "timeout"
