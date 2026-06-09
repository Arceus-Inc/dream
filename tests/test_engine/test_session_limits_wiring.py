"""Spec 13D.2 — run_session aborts when a session limit is breached.

The limiter counts tokens (per-turn usage) and tool-calls (per dispatch) at the
session level and ends the session with SessionEnd(reason="limit-exceeded:..").
With no limiter configured, sessions are unaffected.
"""

from __future__ import annotations

from collections.abc import Sequence

from dream.engine._cost import UsageSnapshot
from dream.engine._messages import ConversationMessage, TextBlock, ToolUseBlock
from dream.engine._records import SessionEnd, TurnRecord
from dream.engine._session import SessionConfig, SessionEvent, run_session
from dream.permissions import SessionLimiter, SessionLimits
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


async def _run(config: SessionConfig) -> list[SessionEvent]:
    return [ev async for ev in run_session(config, [_user("hi")])]


def _end_reason(events: Sequence[SessionEvent]) -> str | None:
    ends = [e for e in events if isinstance(e, SessionEnd)]
    return ends[-1].reason if ends else None


async def test_token_cap_aborts_session() -> None:
    streamer = FakeStreamer([FakeTurn(usage=UsageSnapshot(input_tokens=60, output_tokens=60))])
    config = SessionConfig(
        client=streamer,
        tools=FakeDispatcher(),
        max_turns=4,
        session_id="s",
        limiter=SessionLimiter(SessionLimits(max_llm_tokens=100)),
    )
    assert _end_reason(await _run(config)) == "limit-exceeded:llm_tokens"


async def test_tool_call_cap_aborts_session() -> None:
    turn = FakeTurn(
        tool_uses=[
            ToolUseBlock(id="a", name="bash", input={}),
            ToolUseBlock(id="b", name="bash", input={}),
        ]
    )
    streamer = FakeStreamer([turn, FakeTurn()])
    config = SessionConfig(
        client=streamer,
        tools=FakeDispatcher(),
        max_turns=4,
        session_id="s",
        limiter=SessionLimiter(SessionLimits(max_tool_calls=2)),
    )
    assert _end_reason(await _run(config)) == "limit-exceeded:tool_calls"


async def test_under_limit_does_not_abort() -> None:
    streamer = FakeStreamer([FakeTurn(usage=UsageSnapshot(input_tokens=1, output_tokens=1))])
    config = SessionConfig(
        client=streamer,
        tools=FakeDispatcher(),
        max_turns=4,
        session_id="s",
        limiter=SessionLimiter(SessionLimits()),
    )
    reason = _end_reason(await _run(config)) or ""
    assert not reason.startswith("limit-exceeded")


async def test_successful_turn_is_checkpointed_before_limit_abort() -> None:
    """A turn that completes successfully but trips the token cap must still be
    checkpointed; otherwise the latest good snapshot is dropped and resume
    re-runs already-completed work."""
    checkpoints: list[TurnRecord] = []
    streamer = FakeStreamer([FakeTurn(usage=UsageSnapshot(input_tokens=60, output_tokens=60))])
    config = SessionConfig(
        client=streamer,
        tools=FakeDispatcher(),
        max_turns=4,
        session_id="s",
        limiter=SessionLimiter(SessionLimits(max_llm_tokens=100)),
        checkpoint=checkpoints.append,
    )
    events = await _run(config)
    assert _end_reason(events) == "limit-exceeded:llm_tokens"
    assert len(checkpoints) == 1
    assert checkpoints[0].outcome == "complete"


async def test_no_limiter_is_unaffected() -> None:
    streamer = FakeStreamer(
        [FakeTurn(usage=UsageSnapshot(input_tokens=10**9, output_tokens=10**9))]
    )
    config = SessionConfig(client=streamer, tools=FakeDispatcher(), max_turns=4, session_id="s")
    reason = _end_reason(await _run(config)) or ""
    assert not reason.startswith("limit-exceeded")
