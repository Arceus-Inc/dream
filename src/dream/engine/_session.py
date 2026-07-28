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
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Coroutine,
)
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from dream.contracts.hook import HookEvent
from dream.contracts.provider import ProviderCapabilities
from dream.engine._cost import UsageSnapshot
from dream.engine._events import (
    AssistantTurnComplete,
    CompactionDoneEvent,
    StatusEvent,
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
    TextBlock,
    has_pending_continuation,
    sanitize_conversation_messages,
)
from dream.engine._orientation import OrientationConfig, run_orientation
from dream.engine._outcomes import SessionOutcome, TurnEndKind, TurnOutcome, VerificationResult
from dream.engine._records import (
    SessionEnd,
    TurnRecord,
)
from dream.engine._reviewer import ReviewerConfig
from dream.engine._transitions import TransitionBus, TransitionEvent
from dream.hooks import HookExecutor
from dream.observability._events import state_transition_attrs, validator_finding_attrs
from dream.observability._tracer import NoopTracer, Tracer
from dream.permissions import SessionLimiter
from dream.services.compact import DEFAULT_KEEP_RECENT
from dream.services.compact._attachments import CompactionResult
from dream.services.compact._carryover import refresh_carryover_from_workspace
from dream.services.compact._carryover_state import CarryoverMetadata, UtilisationRatio
from dream.services.compact._orchestrator import (
    AutoCompactState,
    SummariserFn,
    auto_compact_if_needed_async,
    begin_turn,
)
from dream.services.token_estimation import (
    estimate_conversation_tokens,
    should_auto_compact,
    utilisation,
)


def _default_now() -> datetime:
    return datetime.now(UTC)


CONTEXT_PRESSURE_REASON = (
    "context-pressure: transcript still over threshold after compaction"
)


def _build_compaction_done_event(
    transcript_before: list[ConversationMessage],
    new_transcript: list[ConversationMessage],
    result: CompactionResult,
    capabilities: ProviderCapabilities | None,
) -> CompactionDoneEvent:
    pre_count = len(transcript_before)
    pre_tokens = estimate_conversation_tokens(transcript_before)
    post_tokens = estimate_conversation_tokens(new_transcript)
    return CompactionDoneEvent(
        tier=result.tier,
        removed_messages=max(0, pre_count - len(new_transcript)),
        freed_tokens=max(0, pre_tokens - post_tokens),
        resulting_utilisation=utilisation(new_transcript, capabilities),
    )


async def _emit_post_compact_hook(
    config: SessionConfig,
    event: CompactionDoneEvent,
) -> None:
    await _fire_event(
        config.hook_executor,
        HookEvent.POST_COMPACT,
        {
            "session_id": config.session_id,
            "tier": event.tier,
            "removed_messages": event.removed_messages,
            "freed_tokens": event.freed_tokens,
        },
    )


async def _invoke_auto_compact(
    transcript: list[ConversationMessage],
    config: SessionConfig,
    *,
    force: bool,
    fire_pre_compact: bool,
) -> tuple[list[ConversationMessage], CompactionDoneEvent | None]:
    assert config.compactor is not None
    refresh_carryover_from_workspace(config.working_dir, config.carryover_metadata)
    pre_count = len(transcript)
    pre_tokens = estimate_conversation_tokens(transcript)
    if fire_pre_compact and should_auto_compact(
        transcript, config.compaction_capabilities, threshold=config.compaction_threshold
    ):
        await _fire_event(
            config.hook_executor,
            HookEvent.PRE_COMPACT,
            {"session_id": config.session_id, "message_count": pre_count, "token_count": pre_tokens},
        )
    new_transcript, result = await auto_compact_if_needed_async(
        transcript,
        capabilities=config.compaction_capabilities,
        state=config.compactor,
        threshold=config.compaction_threshold,
        preserve_recent=config.compaction_preserve_recent,
        summariser=config.compaction_summariser,
        carryover_metadata=config.carryover_metadata,
        working_dir=config.working_dir,
        force=force,
    )
    if result is None:
        return transcript, None
    event = _build_compaction_done_event(
        transcript, new_transcript, result, config.compaction_capabilities
    )
    await _emit_post_compact_hook(config, event)
    return new_transcript, event


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
    compactor: AutoCompactState | None = None
    compaction_threshold: UtilisationRatio = 0.7
    compaction_preserve_recent: int = DEFAULT_KEEP_RECENT
    compaction_capabilities: ProviderCapabilities | None = None
    compaction_summariser: SummariserFn | None = None
    carryover_metadata: CarryoverMetadata | None = None
    working_dir: Path | None = None
    # Hermes-style hygiene fence: end-of-turn compact when utilisation crosses
    # this threshold (default 85%). ``None`` disables the fence.
    compaction_hygiene_threshold: UtilisationRatio | None = 0.85
    tracer: Tracer = field(default_factory=NoopTracer)
    model: str = ""
    # Spec 13D: hard per-session limits. A fresh limiter per session means
    # counters never roll forward; ``None`` disables enforcement.
    limiter: SessionLimiter | None = None
    # Spec 13: optional lifecycle hook executor. When set, SESSION_START fires
    # once at session entry and STOP fires once before the terminal SessionEnd
    # on every exit path (validator-block, abort, normal seal). Opt-in
    # ``allow_continue`` hooks on STOP may nudge another turn (Hermes pre_verify).
    # ``None`` disables firing, leaving the loop unchanged.
    hook_executor: HookExecutor | None = None
    # Bound for STOP continue nudges (Hermes max_verify_nudges default 3).
    max_verify_nudges: int = 3
    # Role name (planner/generator/evaluator) for role-scoped STOP hooks.
    role: str | None = None

    def __post_init__(self) -> None:
        # 0/negative would satisfy ``consecutive_timeouts >= max`` immediately
        # (it starts at 0), aborting after the first turn even on success.
        if self.max_consecutive_timeouts < 1:
            raise ValueError("max_consecutive_timeouts must be >= 1")


SessionEvent = StreamEvent | TransitionEvent | TurnRecord | SessionEnd


def _fire(bus: TransitionBus | None, event: TransitionEvent) -> TransitionEvent:
    if bus is not None:
        bus.fire(event)
    return event


async def _fire_lifecycle(
    executor: HookExecutor | None, event: HookEvent, session_id: str
) -> None:
    """Fire a session-scoped spec-13 lifecycle hook (SESSION_START / STOP).

    A no-op when no executor is configured. ``executor.fire`` never raises —
    it is crash-isolated and deadline-bounded — so this can't break the loop.
    These events are distinct from the turn-FSM ``TransitionBus`` (state
    transitions); they are the lifecycle observer seam, not the FSM seam.
    """
    if executor is None:
        return
    await executor.fire(event, {"session_id": session_id})


async def _fire_event(
    executor: HookExecutor | None, event: HookEvent, payload: dict[str, Any]
) -> None:
    """Fire a spec-13 hook carrying a richer payload than the bare session id.

    Same observer-only, crash-isolated contract as :func:`_fire_lifecycle`
    (``fire`` never raises); a no-op when no executor is configured. Used for
    USER_PROMPT_SUBMIT / PRE_COMPACT / POST_COMPACT whose payloads carry the
    prompt text or the compaction token deltas.
    """
    if executor is None:
        return
    await executor.fire(event, payload)


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
                try:
                    ev = await _race_next_or_coma(_next, monitor_task)
                except StopAsyncIteration:
                    return
                yield ev
        finally:
            if not monitor_task.done():
                monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await monitor_task


async def _race_next_or_coma(
    next_factory: Callable[[], Coroutine[object, object, StreamEvent]],
    monitor_task: asyncio.Task[None],
) -> StreamEvent:
    """Race the next inner event against the heartbeat monitor.

    Returns the next ``StreamEvent`` when the stream wins. Raises
    ``StopAsyncIteration`` when the inner stream is exhausted, or re-raises
    ``ComaDetected`` (from ``monitor_task``) when the heartbeat trips first —
    cancelling the in-flight ``__anext__`` before doing so.
    """
    next_task: asyncio.Task[StreamEvent] = asyncio.create_task(next_factory())
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
        raise AssertionError(
            "monitor_task completed without raising ComaDetected"
        )
    return next_task.result()


TurnOutcomeKind = TurnEndKind
"""Alias retained for internal session driver call sites."""


@dataclass(frozen=True)
class _TurnResult:
    """Everything the orchestrator needs to know about one driven turn.

    Yielded as the final item of :func:`_drive_one_turn` (after its stream of
    ``StreamEvent``s) so the caller can separate it by ``isinstance``.
    """

    kind: TurnEndKind
    tools_called: tuple[str, ...]
    usage: UsageSnapshot
    started_at: datetime
    ended_at: datetime
    error_message: str | None = None


def _turn_record_from_result(
    turn_number: int,
    result: _TurnResult,
    *,
    outcome: TurnOutcome,
) -> TurnRecord:
    return TurnRecord(
        turn_number=turn_number,
        started_at=result.started_at,
        ended_at=result.ended_at,
        tools_called=result.tools_called,
        verification_result=VerificationResult.SKIPPED,
        outcome=outcome,
        usage=result.usage,
    )


# What :func:`_select_turn_driver` decided should happen next.
_DriverAction = Literal["drive", "seal", "seal-with-warnings"]


@dataclass(frozen=True)
class _DriverDecision:
    action: _DriverAction
    review_item: list[str] | None = None  # the verdict items, when a review ran


async def _select_turn_driver(
    transcript: list[ConversationMessage],
    user_messages: list[ConversationMessage],
    *,
    user_idx: int,
    turn_number: int,
    reviewer: ReviewerConfig | None,
    review_rounds: int,
) -> tuple[_DriverDecision, int]:
    """Decide what drives the next turn; returns ``(decision, new_user_idx)``.

    Mutates ``transcript`` in place by appending the next user / review message
    when one is consumed (matching the original inline behaviour). The driver
    rules, in order:

    1. Pending continuation -> re-enter the model with no new user message.
    2. A user message available -> consume it.
    3. Otherwise ask the reviewer (if any): ``accept`` -> seal;
       ``request_changes`` -> inject + re-enter; ``max_rounds`` -> warnings.
    """
    if has_pending_continuation(transcript):
        return _DriverDecision("drive"), user_idx
    if user_idx < len(user_messages):
        transcript.append(user_messages[user_idx])
        return _DriverDecision("drive"), user_idx + 1
    if reviewer is None or turn_number == 0:
        return _DriverDecision("seal"), user_idx
    verdict = await reviewer.reviewer.review(transcript)
    if verdict.verdict == "accept":
        return _DriverDecision("seal"), user_idx
    items = list(verdict.items)
    if review_rounds + 1 >= reviewer.max_rounds:
        return _DriverDecision("seal-with-warnings", review_item=items), user_idx
    transcript.append(verdict.to_user_message())
    return _DriverDecision("drive", review_item=items), user_idx


async def _maybe_compact(
    transcript: list[ConversationMessage],
    config: SessionConfig,
    *,
    turn_number: int,
) -> tuple[list[ConversationMessage], CompactionDoneEvent | None]:
    """Run per-turn auto-compaction (Spec 04 #1) if a compactor is configured.

    Returns ``(transcript, event)`` — the (possibly compacted) transcript and a
    ``CompactionDoneEvent`` to emit, or ``None`` when nothing was compacted.

    Fires spec-13 ``PRE_COMPACT`` before the transform (gated on the same
    threshold predicate the orchestrator uses) and ``POST_COMPACT`` after —
    observer-only, so a hook cannot stop compaction.
    """
    if config.compactor is None:
        return transcript, None
    begin_turn(config.compactor, turn_id=f"t{turn_number}")
    return await _invoke_auto_compact(
        transcript, config, force=False, fire_pre_compact=True
    )


def _is_still_under_context_pressure(
    transcript: list[ConversationMessage],
    config: SessionConfig,
) -> bool:
    """True when compaction ran this turn but utilisation remains at/above threshold."""
    if config.compactor is None or not config.compactor.compacted_this_turn:
        return False
    return should_auto_compact(
        transcript,
        config.compaction_capabilities,
        threshold=config.compaction_threshold,
    )


async def _maybe_hygiene_compact(
    transcript: list[ConversationMessage],
    config: SessionConfig,
) -> tuple[list[ConversationMessage], CompactionDoneEvent | None]:
    """End-of-turn hygiene fence (Hermes ~85% analogue).

    Runs after a successful act loop when utilisation crosses
    ``compaction_hygiene_threshold``. Uses ``force=True`` so it can run even
    when start-of-turn auto compaction already fired this turn.
    """
    if (
        config.compactor is None
        or config.compaction_hygiene_threshold is None
    ):
        return transcript, None
    if utilisation(transcript, config.compaction_capabilities) < config.compaction_hygiene_threshold:
        return transcript, None

    return await _invoke_auto_compact(
        transcript, config, force=True, fire_pre_compact=True
    )


async def _yield_context_pressure_turn(
    config: SessionConfig,
    *,
    turn_number: int,
    transitions: TransitionBus | None,
) -> AsyncIterator[SessionEvent | _TurnResult]:
    """Skip the act loop when compaction could not free enough room (Spec 04 #4)."""
    turn_started_at = config.now()
    yield _fire(transitions, _turn_transition(TurnState.READ, TurnState.PLAN))
    yield _fire(transitions, _turn_transition(TurnState.PLAN, TurnState.ACT))
    yield StatusEvent(
        message=(
            f"context pressure (turn {turn_number}): "
            "skipping act loop after compaction"
        )
    )
    yield _fire(transitions, _turn_transition(TurnState.ACT, TurnState.VERIFY))
    yield _fire(transitions, _turn_transition(TurnState.VERIFY, TurnState.RECORD))
    yield _TurnResult(
        kind=TurnEndKind.CONTEXT_PRESSURE,
        tools_called=(),
        usage=UsageSnapshot(),
        started_at=turn_started_at,
        ended_at=config.now(),
    )


async def _drive_one_turn(
    config: SessionConfig,
    transcript: list[ConversationMessage],
    *,
    transitions: TransitionBus | None,
) -> AsyncIterator[SessionEvent | _TurnResult]:
    """Drive the act-loop for one turn, yielding its events then a ``_TurnResult``.

    Fires the ``read->plan->act`` and ``act->verify->record`` FSM transitions
    around the streamed events, accumulates the tools called + usage, and
    classifies the terminal condition into a typed :class:`_TurnResult`
    (``TurnEndKind``). The final yielded item is always the ``_TurnResult``; everything before it is a ``SessionEvent`` to relay.
    """
    yield _fire(transitions, _turn_transition(TurnState.READ, TurnState.PLAN))
    yield _fire(transitions, _turn_transition(TurnState.PLAN, TurnState.ACT))

    turn_started_at = config.now()
    tools_called: list[str] = []
    turn_usage = UsageSnapshot()
    kind: TurnEndKind = TurnEndKind.COMPLETE
    error_message: str | None = None

    ctx = QueryContext(
        client=config.client,
        tools=config.tools,
        max_turns=config.max_turns,
        tracer=config.tracer,
        model=config.model,
        ptl_preserve_recent=(
            config.compaction_preserve_recent if config.compactor is not None else None
        ),
    )
    # ``run_query`` is declared as ``AsyncIterator`` but is in fact an async
    # generator; the cast lets us call ``aclose()`` on timeout to release the
    # inner streamer promptly.
    inner = cast(AsyncGenerator[StreamEvent, None], run_query(ctx, transcript))
    try:
        async with asyncio.timeout(config.turn_timeout_seconds):
            async for ev in _drive_turn_with_heartbeat(inner, config.heartbeat):
                yield ev
                if isinstance(ev, ToolExecutionStarted):
                    tools_called.append(ev.tool)
                elif isinstance(ev, AssistantTurnComplete):
                    turn_usage = turn_usage + ev.usage
    except ComaDetected:
        kind = TurnEndKind.COMA
        with contextlib.suppress(Exception):
            await inner.aclose()
    except TimeoutError:
        kind = TurnEndKind.TIMEOUT
        await inner.aclose()
    except Exception as exc:
        # Any non-timeout failure still owes the caller a terminal SessionEnd
        # (emitted via the abort path) rather than crashing the stream. The
        # catch stays broad here because ``run_query`` already narrows provider/
        # transport faults to ``ErrorEvent``; what reaches this seam is an
        # unexpected infra error we must still surface as a structured abort.
        kind = TurnEndKind.ERROR
        error_message = f"error: {exc}"
        with contextlib.suppress(Exception):
            await inner.aclose()

    yield _fire(transitions, _turn_transition(TurnState.ACT, TurnState.VERIFY))
    yield _fire(transitions, _turn_transition(TurnState.VERIFY, TurnState.RECORD))

    yield _TurnResult(
        kind=kind,
        tools_called=tuple(tools_called),
        usage=turn_usage,
        started_at=turn_started_at,
        ended_at=config.now(),
        error_message=error_message,
    )


def _classify_turn_outcome(
    kind: TurnEndKind, *, consecutive_timeouts: int
) -> tuple[TurnOutcome, int]:
    """Map a turn's typed end-kind to a recorded ``TurnOutcome`` + timeout streak."""
    if kind in (TurnEndKind.COMA, TurnEndKind.ERROR):
        return TurnOutcome.ABORTED, 0
    if kind == TurnEndKind.TIMEOUT:
        return TurnOutcome.TIMEOUT, consecutive_timeouts + 1
    if kind == TurnEndKind.CONTEXT_PRESSURE:
        return TurnOutcome.CONTEXT_PRESSURE, 0
    return TurnOutcome.COMPLETE, 0


def _check_abort_conditions(
    result: _TurnResult,
    *,
    limiter: SessionLimiter | None,
    consecutive_timeouts: int,
    max_consecutive_timeouts: int,
) -> str | None:
    """Return an abort reason if this turn should end the session, else ``None``.

    Order matters and matches the original inline checks: hard-cap breach first
    (so a ``complete`` turn that trips a cap still aborts), then error, then
    coma, then the repeated-timeout streak.
    """
    if limiter is not None:
        limiter.record_tokens(result.usage.input_tokens + result.usage.output_tokens)
        for _ in result.tools_called:
            limiter.record_tool_call()
        if (breached := limiter.breached()) is not None:
            return breached
    if result.kind == TurnEndKind.ERROR:
        return result.error_message
    if result.kind == TurnEndKind.COMA:
        return "coma"
    if consecutive_timeouts >= max_consecutive_timeouts:
        return "repeated-timeout"
    return None


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

    # Spec 13: SESSION_START fires once at session entry, before any turn work.
    # Observer-only and crash-isolated (the executor swallows), so a faulty
    # hook can't delay or abort the session.
    await _fire_lifecycle(config.hook_executor, HookEvent.SESSION_START, config.session_id)

    # Spec 13: USER_PROMPT_SUBMIT fires once the initiating prompt is known,
    # right after SESSION_START. Payload carries the prompt text so a hook can
    # log / gate / annotate; observer-only, so it can't rewrite the prompt.
    if user_messages:
        await _fire_event(
            config.hook_executor,
            HookEvent.USER_PROMPT_SUBMIT,
            {"session_id": config.session_id, "prompt": user_messages[0].text},
        )

    # Mirror every FSM transition into the trace (Spec 12a). Registered before
    # the first ``_fire`` so the starting->orienting edge is captured too; a
    # NoopTracer makes this a cheap no-op when tracing is off.
    if transitions is not None:
        transitions.register(
            lambda ev: config.tracer.event(
                "state.transition",
                state_transition_attrs(
                    kind=ev.kind, from_state=ev.from_state, to_state=ev.to_state
                ),
            )
        )

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
        for finding in brief.validator_findings:
            config.tracer.event(
                "validator.finding",
                validator_finding_attrs(
                    severity=finding.severity,
                    code=finding.code,
                    message=finding.message,
                    path=finding.path,
                ),
            )
        if brief.has_blocking_findings:
            yield _fire(
                transitions,
                _session_transition(SessionState.ORIENTING, SessionState.ABORTED),
            )
            await _fire_lifecycle(config.hook_executor, HookEvent.STOP, config.session_id)
            yield SessionEnd(
                session_id=config.session_id,
                started_at=session_started_at,
                ended_at=config.now(),
                turns=0,
                total_usage=UsageSnapshot(),
                outcome=SessionOutcome.ABORTED,
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
    verify_nudges = 0
    stop_fired_for_seal = False

    while True:
        decision, user_idx = await _select_turn_driver(
            transcript,
            user_messages,
            user_idx=user_idx,
            turn_number=turn_number,
            reviewer=config.reviewer,
            review_rounds=review_rounds,
        )
        if decision.review_item is not None:
            review_rounds += 1
            last_review_items = decision.review_item
        if decision.action in {"seal", "seal-with-warnings"}:
            # Hermes-style STOP continue: allow_continue hooks may inject a
            # synthetic user nudge and keep the session working (≤ max_verify_nudges).
            if (
                config.hook_executor is not None
                and verify_nudges < config.max_verify_nudges
            ):
                stop_outcome = await config.hook_executor.fire(
                    HookEvent.STOP,
                    {
                        "session_id": config.session_id,
                        "phase": "pre_seal",
                        "verify_nudges": verify_nudges,
                        "role": config.role,
                    },
                )
                if stop_outcome.continue_message:
                    verify_nudges += 1
                    nudge = ConversationMessage(
                        role="user",
                        content=[TextBlock(text=stop_outcome.continue_message)],
                    )
                    transcript.append(nudge)
                    user_messages = [*user_messages, nudge]
                    continue
                stop_fired_for_seal = True
            if decision.action == "seal-with-warnings":
                seal_warnings = True
            break

        turn_number += 1
        if turn_number > 1:
            yield _fire(
                transitions,
                _session_transition(SessionState.WORKING, SessionState.WORKING),
            )

        # Spec 04 #1: per-turn auto-compaction. Runs after the new user
        # message has been appended and before the model is re-entered, so
        # the streamer sees the post-compaction transcript.
        transcript, compaction_event = await _maybe_compact(
            transcript, config, turn_number=turn_number
        )
        if compaction_event is not None:
            yield compaction_event

        if _is_still_under_context_pressure(transcript, config):
            pressure_result: _TurnResult | None = None
            async for item in _yield_context_pressure_turn(
                config, turn_number=turn_number, transitions=transitions
            ):
                if isinstance(item, _TurnResult):
                    pressure_result = item
                else:
                    yield item
            assert pressure_result is not None
            yield _turn_record_from_result(
                turn_number,
                pressure_result,
                outcome=TurnOutcome.CONTEXT_PRESSURE,
            )
            seal_warnings = True
            last_review_items = [CONTEXT_PRESSURE_REASON]
            break

        turn_result: _TurnResult | None = None
        async for item in _drive_one_turn(config, transcript, transitions=transitions):
            if isinstance(item, _TurnResult):
                turn_result = item
            else:
                yield item
        assert turn_result is not None  # _drive_one_turn always yields one last

        if turn_result.kind != TurnEndKind.COMPLETE:
            # The turn was cancelled/failed mid-flight: run_query may have appended
            # an assistant tool_use with no matching tool_result. Re-sanitize so the
            # next turn never re-enters the model with a dangling tool-call atom.
            transcript = sanitize_conversation_messages(transcript)

        turn_outcome, consecutive_timeouts = _classify_turn_outcome(
            turn_result.kind, consecutive_timeouts=consecutive_timeouts
        )

        record = _turn_record_from_result(
            turn_number, turn_result, outcome=turn_outcome
        )
        yield record
        total_usage = total_usage + turn_result.usage

        if turn_outcome == TurnOutcome.COMPLETE and config.checkpoint is not None:
            with contextlib.suppress(Exception):
                config.checkpoint(record)

        if turn_result.kind == TurnEndKind.COMPLETE:
            transcript, hygiene_event = await _maybe_hygiene_compact(transcript, config)
            if hygiene_event is not None:
                yield hygiene_event

        # Spec 13D + abort scan: count usage/tool-calls against the hard caps and
        # classify any terminal condition into a single abort reason.
        abort_reason = _check_abort_conditions(
            turn_result,
            limiter=config.limiter,
            consecutive_timeouts=consecutive_timeouts,
            max_consecutive_timeouts=config.max_consecutive_timeouts,
        )
        if abort_reason is not None:
            break

    if abort_reason is not None:
        yield _fire(
            transitions,
            _session_transition(SessionState.WORKING, SessionState.ABORTED),
        )
        await _fire_lifecycle(config.hook_executor, HookEvent.STOP, config.session_id)
        yield SessionEnd(
            session_id=config.session_id,
            started_at=session_started_at,
            ended_at=config.now(),
            turns=turn_number,
            total_usage=total_usage,
            outcome=SessionOutcome.ABORTED,
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
        session_outcome = SessionOutcome.DONE_WITH_WARNINGS
        reason = (
            "unresolved: " + "; ".join(last_review_items)
            if last_review_items
            else "unresolved (no items)"
        )
    else:
        session_outcome = SessionOutcome.DONE
        reason = None

    # Avoid double-STOP when we already fired at pre_seal (continue check).
    if not stop_fired_for_seal:
        await _fire_lifecycle(config.hook_executor, HookEvent.STOP, config.session_id)
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
