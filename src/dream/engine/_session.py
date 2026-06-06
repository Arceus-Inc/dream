"""``run_session`` — the outer session orchestrator (Spec 03 stages 3a + 3b).

Wraps the inner ``run_query`` act-loop with the session FSM:
``starting -> orienting -> working*N -> sealing -> done | done-with-warnings
| aborted``.

Each user message (and each reviewer-driven re-entry) drives one turn;
each turn walks ``read -> plan -> act -> verify -> record`` and produces
exactly one ``TurnRecord``. Every transition is fired on the optional
``TransitionBus`` and also yielded inline so callers that don't want to
register a bus can filter on ``isinstance(ev, TransitionEvent)``.

Stage 3a brought the deterministic machinery (FSM, timeouts, records,
crash resume, hook bus, checkpoints). Stage 3b layers the rituals on
top, all opt-in via optional ``SessionConfig`` fields:

- ``orientation`` — runs the orientation ritual once at session start,
  prepends the brief, aborts on blocking validator findings.
- ``heartbeat`` — wraps each turn's stream with a coma detector; a
  ``ComaDetected`` cancels the turn and aborts the session with
  ``reason="coma"``.
- ``reviewer`` — once user messages are exhausted, runs the
  Ralph-Wiggum loop: ``accept`` -> seal as ``done``; ``request_changes``
  injects items and re-enters ``working``; at ``max_rounds`` it
  force-closes as ``done-with-warnings``.

A real provider adapter for ``TurnStreamer`` is stage 3c.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

from dream.engine._cost import UsageSnapshot
from dream.engine._events import (
    AssistantTurnComplete,
    StreamEvent,
    ToolExecutionStarted,
)
from dream.engine._fsm import (
    SessionState,
    TurnState,
    is_valid_session_transition,
    is_valid_turn_transition,
)
from dream.engine._heartbeat import (
    ComaDetected,
    HeartbeatConfig,
    HeartbeatMonitor,
)
from dream.engine._loop import (
    QueryContext,
    ToolDispatcher,
    TurnStreamer,
    run_query,
)
from dream.engine._messages import (
    ConversationMessage,
    has_pending_continuation,
    sanitize_conversation_messages,
)
from dream.engine._orientation import OrientationConfig, run_orientation
from dream.engine._records import (
    SessionEnd,
    SessionOutcome,
    TurnOutcome,
    TurnRecord,
)
from dream.engine._reviewer import ReviewerConfig
from dream.engine._transitions import TransitionBus, TransitionEvent


def _default_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class SessionConfig:
    """Everything ``run_session`` needs for one session.

    ``now`` is injectable so tests get a deterministic timestamp stream;
    production defaults to UTC wall-clock. ``orientation`` / ``heartbeat``
    / ``reviewer`` are the stage 3b ritual hooks; each defaults to
    ``None`` so a config with only the 3a fields behaves identically to
    the 3a orchestrator.
    """

    client: TurnStreamer
    tools: ToolDispatcher
    max_turns: int = 8
    turn_timeout_seconds: float | None = None
    max_consecutive_timeouts: int = 3
    session_id: str = "s_default"
    checkpoint: Callable[[TurnRecord], None] | None = None
    now: Callable[[], datetime] = field(default=_default_now)
    orientation: OrientationConfig | None = None
    heartbeat: HeartbeatConfig | None = None
    reviewer: ReviewerConfig | None = None

    def __post_init__(self) -> None:
        # 0/negative would satisfy ``consecutive_timeouts >= max`` immediately
        # (it starts at 0), aborting after the first turn even on success.
        if self.max_consecutive_timeouts < 1:
            raise ValueError("max_consecutive_timeouts must be >= 1")


SessionEvent = StreamEvent | TransitionEvent | TurnRecord | SessionEnd


def _fire(
    bus: TransitionBus | None, event: TransitionEvent
) -> TransitionEvent:
    if bus is not None:
        bus.fire(event)
    return event


def _session_transition(src: SessionState, dst: SessionState) -> TransitionEvent:
    """Build a session ``TransitionEvent``, rejecting any edge not in the FSM table.

    Consulting :func:`is_valid_session_transition` here is what makes an illegal
    walk impossible *by construction* — a typo or a future code path that tries
    an unlisted edge raises instead of silently emitting a wrong event. The
    states are passed as :class:`SessionState` members, not bare strings, so the
    edge is checked against the enum, never a stringly-typed literal.
    """
    if not is_valid_session_transition(src, dst):
        raise ValueError(f"illegal session transition: {src.value} -> {dst.value}")
    return TransitionEvent(kind="session", from_state=src, to_state=dst)


def _turn_transition(src: TurnState, dst: TurnState) -> TransitionEvent:
    """Build a turn ``TransitionEvent``, rejecting any edge not in the FSM table."""
    if not is_valid_turn_transition(src, dst):
        raise ValueError(f"illegal turn transition: {src.value} -> {dst.value}")
    return TransitionEvent(kind="turn", from_state=src, to_state=dst)


async def _drive_turn_with_heartbeat(
    inner: AsyncGenerator[StreamEvent, None],
    heartbeat: HeartbeatConfig | None,
) -> AsyncIterator[StreamEvent]:
    """Yield events from ``inner`` while a heartbeat polls in the background.

    When the heartbeat trips, the in-flight ``__anext__`` task is
    cancelled and ``ComaDetected`` is raised so the caller can
    short-circuit the turn. With ``heartbeat=None`` this degenerates to
    a plain ``async for`` over ``inner``.
    """
    # ``aclosing`` guarantees ``inner.aclose()`` runs on *every* exit — normal
    # completion, an exception, an external cancellation propagating through the
    # ``yield`` below, or our consumer calling ``aclose()`` on us. Without it, an
    # outer cancel would abandon ``inner`` (the act-loop) with its provider
    # stream — and the httpx connection underneath it — never released.
    async with contextlib.aclosing(inner):
        if heartbeat is None:
            async for ev in inner:
                yield ev
            return

        monitor = HeartbeatMonitor(
            health=heartbeat.health,
            interval=heartbeat.interval_seconds,
            threshold=heartbeat.failure_threshold,
        )
        monitor_task = asyncio.create_task(monitor.run())
        aiter_ = inner.__aiter__()

        async def _next() -> StreamEvent:
            return await aiter_.__anext__()

        try:
            while True:
                next_task: asyncio.Task[StreamEvent] = asyncio.create_task(_next())
                done, _pending = await asyncio.wait(
                    {next_task, monitor_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if monitor_task in done:
                    next_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await next_task
                    # Re-raise ComaDetected from the monitor task.
                    monitor_task.result()
                    return  # unreachable; appeases the type checker
                try:
                    ev = next_task.result()
                except StopAsyncIteration:
                    return
                yield ev
        finally:
            if not monitor_task.done():
                monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await monitor_task


async def run_session(
    config: SessionConfig,
    user_messages: list[ConversationMessage],
    *,
    resume_messages: list[ConversationMessage] | None = None,
    transitions: TransitionBus | None = None,
) -> AsyncIterator[SessionEvent]:
    """Run a single session end-to-end. Yields a stream of session events.

    The caller dispatches on ``isinstance`` — ``TransitionEvent`` for
    audit/UI, ``TurnRecord`` for the jsonl trail, ``SessionEnd`` as the
    end-of-stream sentinel, everything else (``StreamEvent``) for live
    rendering.
    """
    session_started_at = config.now()

    # starting -> orienting (always fired; the orientation ritual runs
    # between this transition and the orienting -> working one below).
    yield _fire(
        transitions,
        _session_transition(SessionState.STARTING, SessionState.ORIENTING),
    )

    # Seed the transcript. ``sanitize`` drops any dangling trailing tool_use
    # so the resumed turn re-enters the model on a clean atom boundary.
    transcript: list[ConversationMessage] = (
        sanitize_conversation_messages(resume_messages) if resume_messages else []
    )

    # Orientation ritual: skipped on resume (the prior incarnation
    # already oriented; re-orienting would duplicate the brief and
    # violate "orientation runs exactly once per session").
    if config.orientation is not None and resume_messages is None:
        brief = await run_orientation(config.orientation)
        if brief.has_blocking_findings:
            yield _fire(
                transitions,
                _session_transition(SessionState.ORIENTING, SessionState.ABORTED),
            )
            yield SessionEnd(
                session_id=config.session_id,
                started_at=session_started_at,
                ended_at=config.now(),
                turns=0,
                total_usage=UsageSnapshot(),
                outcome="aborted",
                reason="validator-blocking",
            )
            return
        transcript.insert(0, brief.to_user_message())

    yield _fire(
        transitions,
        _session_transition(SessionState.ORIENTING, SessionState.WORKING),
    )

    total_usage = UsageSnapshot()
    consecutive_timeouts = 0
    turn_number = 0
    user_idx = 0
    abort_reason: str | None = None
    review_rounds = 0
    last_review_items: list[str] = []
    seal_warnings = False

    while True:
        # Decide what drives the next turn:
        # 1. Pending continuation -> re-enter the model with no new user msg.
        # 2. A user message available -> consume it.
        # 3. Otherwise, ask the reviewer (if any). accept -> seal;
        #    request_changes -> inject + re-enter; max_rounds -> warnings.
        if has_pending_continuation(transcript):
            pass
        elif user_idx < len(user_messages):
            transcript.append(user_messages[user_idx])
            user_idx += 1
        else:
            if config.reviewer is None or turn_number == 0:
                break
            verdict = await config.reviewer.reviewer.review(transcript)
            if verdict.verdict == "accept":
                break
            review_rounds += 1
            last_review_items = list(verdict.items)
            if review_rounds >= config.reviewer.max_rounds:
                seal_warnings = True
                break
            transcript.append(verdict.to_user_message())

        turn_number += 1
        if turn_number > 1:
            yield _fire(
                transitions,
                _session_transition(SessionState.WORKING, SessionState.WORKING),
            )

        # Turn FSM: read -> plan -> act -> verify -> record.
        yield _fire(
            transitions,
            _turn_transition(TurnState.READ, TurnState.PLAN),
        )
        yield _fire(
            transitions,
            _turn_transition(TurnState.PLAN, TurnState.ACT),
        )

        turn_started_at = config.now()
        tools_called: list[str] = []
        turn_usage = UsageSnapshot()
        timed_out = False
        turn_coma = False
        turn_error: str | None = None

        ctx = QueryContext(
            client=config.client, tools=config.tools, max_turns=config.max_turns
        )
        # ``run_query`` is declared as ``AsyncIterator`` but is in fact an
        # async generator; the cast lets us call ``aclose()`` on timeout to
        # release the inner streamer promptly.
        inner = cast(
            AsyncGenerator[StreamEvent, None], run_query(ctx, transcript)
        )
        try:
            async with asyncio.timeout(config.turn_timeout_seconds):
                async for ev in _drive_turn_with_heartbeat(
                    inner, config.heartbeat
                ):
                    yield ev
                    if isinstance(ev, ToolExecutionStarted):
                        tools_called.append(ev.tool)
                    elif isinstance(ev, AssistantTurnComplete):
                        turn_usage = turn_usage + ev.usage
        except ComaDetected:
            turn_coma = True
            with contextlib.suppress(Exception):
                await inner.aclose()
        except TimeoutError:
            timed_out = True
            await inner.aclose()
        except Exception as exc:
            # Any non-timeout failure still owes the caller a terminal SessionEnd
            # (emitted via the abort path below) rather than crashing the stream.
            turn_error = f"error: {exc}"
            with contextlib.suppress(Exception):
                await inner.aclose()

        if timed_out or turn_coma or turn_error is not None:
            # The turn was cancelled/failed mid-flight: run_query may have appended
            # an assistant tool_use with no matching tool_result. Re-sanitize so the
            # next turn never re-enters the model with a dangling tool-call atom.
            transcript = sanitize_conversation_messages(transcript)

        yield _fire(
            transitions,
            _turn_transition(TurnState.ACT, TurnState.VERIFY),
        )
        yield _fire(
            transitions,
            _turn_transition(TurnState.VERIFY, TurnState.RECORD),
        )

        turn_ended_at = config.now()
        turn_outcome: TurnOutcome
        if turn_coma or turn_error is not None:
            turn_outcome = "aborted"
            consecutive_timeouts = 0  # any non-timeout outcome breaks the streak
        elif timed_out:
            turn_outcome = "timeout"
            consecutive_timeouts += 1
        else:
            turn_outcome = "complete"
            consecutive_timeouts = 0

        record = TurnRecord(
            turn_number=turn_number,
            started_at=turn_started_at,
            ended_at=turn_ended_at,
            tools_called=tuple(tools_called),
            verification_result="skipped",
            outcome=turn_outcome,
            usage=turn_usage,
        )
        yield record
        total_usage = total_usage + turn_usage

        # Checkpoint only on a successful turn (spec 03 #4).
        # Best-effort: a snapshot writer crash must not break the session.
        if turn_outcome == "complete" and config.checkpoint is not None:
            with contextlib.suppress(Exception):
                config.checkpoint(record)

        if turn_error is not None:
            abort_reason = turn_error
            break

        if turn_coma:
            abort_reason = "coma"
            break

        if consecutive_timeouts >= config.max_consecutive_timeouts:
            abort_reason = "repeated-timeout"
            break

    if abort_reason is not None:
        yield _fire(
            transitions,
            _session_transition(SessionState.WORKING, SessionState.ABORTED),
        )
        yield SessionEnd(
            session_id=config.session_id,
            started_at=session_started_at,
            ended_at=config.now(),
            turns=turn_number,
            total_usage=total_usage,
            outcome="aborted",
            reason=abort_reason,
        )
        return

    yield _fire(
        transitions,
        _session_transition(SessionState.WORKING, SessionState.SEALING),
    )
    yield _fire(
        transitions,
        _session_transition(SessionState.SEALING, SessionState.DONE),
    )

    session_outcome: SessionOutcome
    reason: str | None
    if seal_warnings:
        session_outcome = "done-with-warnings"
        reason = (
            "unresolved: " + "; ".join(last_review_items)
            if last_review_items
            else "unresolved (no items)"
        )
    else:
        session_outcome = "done"
        reason = None

    yield SessionEnd(
        session_id=config.session_id,
        started_at=session_started_at,
        ended_at=config.now(),
        turns=turn_number,
        total_usage=total_usage,
        outcome=session_outcome,
        reason=reason,
    )


__all__ = ["SessionConfig", "SessionEvent", "run_session"]
