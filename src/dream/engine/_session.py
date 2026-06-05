"""``run_session`` — the outer session orchestrator (Spec 03 stage 3a).

Wraps the inner ``run_query`` act-loop with the session FSM:
``starting -> orienting -> working*N -> sealing -> done | aborted``.

Each user message drives one turn; each turn walks
``read → plan → act → verify → record`` and produces exactly one
``TurnRecord``. Every transition is fired on the optional ``TransitionBus``
and also yielded inline so callers that don't want to register a bus can
filter on ``isinstance(ev, TransitionEvent)``.

This stage (3a) is the deterministic machinery only — orientation summary,
heartbeat coma detection, and reviewer back-edges are stage 3b. A real
provider adapter for ``TurnStreamer`` is stage 3c.
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
from dream.engine._records import SessionEnd, TurnRecord
from dream.engine._transitions import TransitionBus, TransitionEvent


def _default_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class SessionConfig:
    """Everything ``run_session`` needs for one session.

    ``now`` is injectable so tests get a deterministic timestamp stream;
    production defaults to UTC wall-clock.
    """

    client: TurnStreamer
    tools: ToolDispatcher
    max_turns: int = 8
    turn_timeout_seconds: float | None = None
    max_consecutive_timeouts: int = 3
    session_id: str = "s_default"
    checkpoint: Callable[[TurnRecord], None] | None = None
    now: Callable[[], datetime] = field(default=_default_now)


SessionEvent = StreamEvent | TransitionEvent | TurnRecord | SessionEnd


def _fire(
    bus: TransitionBus | None, event: TransitionEvent
) -> TransitionEvent:
    if bus is not None:
        bus.fire(event)
    return event


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

    # Substrate transitions: starting → orienting → working.
    yield _fire(
        transitions,
        TransitionEvent(kind="session", from_state="starting", to_state="orienting"),
    )
    yield _fire(
        transitions,
        TransitionEvent(kind="session", from_state="orienting", to_state="working"),
    )

    # Seed the transcript. ``sanitize`` drops any dangling trailing tool_use
    # so the resumed turn re-enters the model on a clean atom boundary.
    transcript: list[ConversationMessage] = (
        sanitize_conversation_messages(resume_messages) if resume_messages else []
    )

    total_usage = UsageSnapshot()
    consecutive_timeouts = 0
    turn_number = 0
    user_idx = 0
    abort_reason: str | None = None

    while True:
        # If the transcript owes the model a continuation we re-enter without
        # consuming a new user message; otherwise we need one to make progress.
        if not has_pending_continuation(transcript):
            if user_idx >= len(user_messages):
                break
            transcript.append(user_messages[user_idx])
            user_idx += 1

        turn_number += 1
        if turn_number > 1:
            yield _fire(
                transitions,
                TransitionEvent(
                    kind="session", from_state="working", to_state="working"
                ),
            )

        # Turn FSM: read → plan → act → verify → record.
        yield _fire(
            transitions,
            TransitionEvent(kind="turn", from_state="read", to_state="plan"),
        )
        yield _fire(
            transitions,
            TransitionEvent(kind="turn", from_state="plan", to_state="act"),
        )

        turn_started_at = config.now()
        tools_called: list[str] = []
        turn_usage = UsageSnapshot()
        timed_out = False

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
                async for ev in inner:
                    yield ev
                    if isinstance(ev, ToolExecutionStarted):
                        tools_called.append(ev.tool)
                    elif isinstance(ev, AssistantTurnComplete):
                        turn_usage = turn_usage + ev.usage
        except TimeoutError:
            timed_out = True
            # Ensure the async generator is closed promptly.
            await inner.aclose()

        yield _fire(
            transitions,
            TransitionEvent(kind="turn", from_state="act", to_state="verify"),
        )
        yield _fire(
            transitions,
            TransitionEvent(kind="turn", from_state="verify", to_state="record"),
        )

        turn_ended_at = config.now()
        if timed_out:
            outcome = "timeout"
            consecutive_timeouts += 1
        else:
            outcome = "complete"
            consecutive_timeouts = 0

        record = TurnRecord(
            turn_number=turn_number,
            started_at=turn_started_at,
            ended_at=turn_ended_at,
            tools_called=tools_called,
            verification_result="skipped",
            outcome=outcome,  # type: ignore[arg-type]
            usage=turn_usage,
        )
        yield record
        total_usage = total_usage + turn_usage

        # Checkpoint only on a successful turn (spec 03 #4).
        # Best-effort: a snapshot writer crash must not break the session.
        if outcome == "complete" and config.checkpoint is not None:
            with contextlib.suppress(Exception):
                config.checkpoint(record)

        if consecutive_timeouts >= config.max_consecutive_timeouts:
            abort_reason = "repeated-timeout"
            break

    if abort_reason is not None:
        yield _fire(
            transitions,
            TransitionEvent(
                kind="session", from_state="working", to_state="aborted"
            ),
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
        TransitionEvent(kind="session", from_state="working", to_state="sealing"),
    )
    yield _fire(
        transitions,
        TransitionEvent(kind="session", from_state="sealing", to_state="done"),
    )
    yield SessionEnd(
        session_id=config.session_id,
        started_at=session_started_at,
        ended_at=config.now(),
        turns=turn_number,
        total_usage=total_usage,
        outcome="done",
    )


__all__ = ["SessionConfig", "SessionEvent", "run_session"]
