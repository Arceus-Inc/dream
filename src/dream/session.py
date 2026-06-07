"""Session: one conversation on a Harness.

The public ``Session`` type. The concrete engine binding lives in
``dream.engine._engine`` (``QueryEngine``); ``Session`` drives the Spec 03
``run_session`` orchestrator against the engine and translates internal
``StreamEvent``s into public ``events.Event``s.

Transcript persistence is mirrored from ``run_session``'s event stream so
a subsequent ``send`` resumes the conversation rather than restarting it:

- a user message holding the new prompt is recorded at the start of each
  ``send`` and passed via ``run_session(..., user_messages=[...])``;
- every ``AssistantTurnComplete`` becomes one assistant message;
- ``ToolExecutionCompleted`` events between consecutive
  ``AssistantTurnComplete`` events are batched into a single user
  message of ``ToolResultBlock``s -- the tool-call atom (Spec 00 #1).

The transcript carries across ``send`` calls; orientation is intentionally
left for a later slice (slice E rituals).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactionDoneEvent,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.engine._session import run_session
from dream.events import (
    Compacted,
    Error,
    Event,
    TextDelta,
    ToolUseResult,
    ToolUseStart,
    TurnComplete,
)

if TYPE_CHECKING:
    from dream.engine._engine import QueryEngine


def _tool_failed_marker(tool_name: str) -> str:
    """Generic, non-revealing tool-failure marker for the transcript.

    Mirrors ``run_query``'s own transcript contract (``_loop.py``): the
    detailed failure stays on the event stream while the transcript the
    model re-reads on resume only ever sees this generic line.
    """
    return f"tool {tool_name!r} failed to execute"


@dataclass(frozen=True)
class SessionOptions:
    """Per-session overrides. All fields optional."""

    model: str | None = None
    system_prompt: str | None = None
    max_turns: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionCost:
    """Running counters surfaced via `Session.cost`."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0


class Session:
    """One conversation against a Harness.

    The engine binding is the ``_engine`` keyword-only argument (the
    leading underscore signals "harness-internal"; external callers
    construct a ``Session`` via ``Harness.start_session``).

    Without an engine, ``send`` raises ``NotImplementedError`` while
    ``cancel`` / ``close`` are no-ops so background apps can call them
    defensively in cleanup blocks before any ``send`` has been issued.
    """

    id: str
    options: SessionOptions
    cost: SessionCost

    def __init__(
        self,
        *,
        id: str,
        options: SessionOptions | None = None,
        _engine: QueryEngine | None = None,
    ) -> None:
        self.id = id
        self.options = options or SessionOptions()
        self.cost = SessionCost()
        self._engine = _engine
        self._transcript: list[ConversationMessage] = []
        self._cancel_event: asyncio.Event | None = None
        self._closed = False
        # Single-flight guard: ``Session`` keeps per-call cancel state on the
        # instance, so two overlapping ``send`` calls would clobber each
        # other and ``cancel`` could target the wrong stream. Only one
        # ``send`` may be in flight at a time (#33).
        self._active = False

    async def send(self, prompt: str) -> AsyncIterator[Event]:
        """Submit a user prompt and stream typed events back.

        Yields one of the public ``events.Event`` types per significant
        event from the underlying ``run_session`` stream; orchestration
        events (``TransitionEvent``, ``TurnRecord``, ``SessionEnd``) are
        filtered.
        """
        if self._closed:
            raise RuntimeError("session is closed")
        if self._engine is None:
            raise NotImplementedError("engine binding not yet implemented")
        if self._active:
            # Single-flight: per-call cancel state lives on the instance, so
            # overlapping sends would corrupt each other's cancel routing.
            raise RuntimeError("a send is already in flight on this session")
        self._active = True

        # ``resume`` is the transcript as we knew it before this call;
        # ``run_session`` sanitizes it on entry. We then append the new
        # user message to our local copy so a follow-up ``send`` started
        # before this one finishes still sees a consistent view.
        resume = list(self._transcript) if self._transcript else None
        user_msg = ConversationMessage(role="user", content=[TextBlock(text=prompt)])
        self._transcript.append(user_msg)

        config = self._engine.make_session_config()
        inner: AsyncGenerator[Any, None] = run_session(  # type: ignore[assignment]
            config, [user_msg], resume_messages=resume
        )
        cancel_event = asyncio.Event()
        self._cancel_event = cancel_event

        pending_tool_results: list[ToolResultBlock] = []

        try:
            # Race ``inner.__anext__`` against the cancel event so a
            # ``Session.cancel`` call from another task interrupts the
            # stream promptly even if the underlying turn is awaiting a
            # long provider response.
            aiter_ = inner.__aiter__()
            while True:
                next_coro: Any = aiter_.__anext__()
                next_task: asyncio.Task[Any] = asyncio.ensure_future(next_coro)
                cancel_task: asyncio.Task[Any] = asyncio.create_task(cancel_event.wait())
                try:
                    _done, _pending = await asyncio.wait(
                        {next_task, cancel_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except BaseException:
                    next_task.cancel()
                    cancel_task.cancel()
                    raise
                cancel_task.cancel()
                with contextlib.suppress(BaseException):
                    await cancel_task

                if cancel_event.is_set():
                    next_task.cancel()
                    with contextlib.suppress(BaseException):
                        await next_task
                    break

                try:
                    ev = next_task.result()
                except StopAsyncIteration:
                    break

                public = self._translate(ev, pending_tool_results)
                if public is None:
                    continue
                yield public
        finally:
            self._cancel_event = None
            self._active = False
            # ``aclose`` is the canonical way to cascade cleanup down
            # through ``run_session`` / ``run_query`` / the provider
            # stream. Safe here because no other task is iterating
            # ``inner`` -- this generator was its sole driver.
            with contextlib.suppress(BaseException):
                await inner.aclose()
            # Flush any tool results buffered but not yet committed to the
            # transcript (the stream ended mid-tool: either normally between
            # ToolExecutionCompleted and the next AssistantTurnComplete, or
            # via cancel). Persisting them on *both* paths keeps the
            # transcript from carrying a dangling assistant tool_use with no
            # matching tool_result, which the model rejects on resume (#34).
            if pending_tool_results:
                self._transcript.append(
                    ConversationMessage(role="user", content=list(pending_tool_results))
                )
                pending_tool_results.clear()

    def _translate(
        self,
        ev: Any,
        pending_tool_results: list[ToolResultBlock],
    ) -> Event | None:
        """Translate one internal event; update transcript + cost state.

        Returns the public event to yield, or ``None`` to filter the event.
        """
        if isinstance(ev, AssistantTextDelta):
            return TextDelta(text=ev.text)

        if isinstance(ev, ToolExecutionStarted):
            return ToolUseStart(tool_use_id=ev.id, name=ev.tool, input=dict(ev.input))

        if isinstance(ev, ToolExecutionCompleted):
            # Transcript vs. event split, mirroring ``run_query``'s contract
            # (#35): the event carries ``ev.result`` verbatim (the engine's
            # observability side-channel), but the transcript the model
            # re-reads on resume must NOT carry internal error detail. For
            # an error result we write the same generic, non-revealing
            # marker the engine writes into its own transcript; a successful
            # result is committed verbatim.
            transcript_content = (
                _tool_failed_marker(ev.tool) if ev.is_error else ev.result
            )
            pending_tool_results.append(
                ToolResultBlock(
                    tool_use_id=ev.id,
                    content=transcript_content,
                    is_error=ev.is_error,
                )
            )
            return ToolUseResult(
                tool_use_id=ev.id,
                name=ev.tool,
                content=ev.result,
                is_error=ev.is_error,
            )

        if isinstance(ev, AssistantTurnComplete):
            # Flush prior turn's tool results as one user message
            # *before* recording the new assistant message; this matches
            # ``run_query``'s own append order.
            if pending_tool_results:
                self._transcript.append(
                    ConversationMessage(role="user", content=list(pending_tool_results))
                )
                pending_tool_results.clear()
            self._transcript.append(ConversationMessage(role="assistant", content=list(ev.blocks)))
            self.cost.input_tokens += ev.usage.input_tokens
            self.cost.output_tokens += ev.usage.output_tokens
            self.cost.cache_read_tokens += ev.usage.cache_read_tokens
            self.cost.cache_write_tokens += ev.usage.cache_write_tokens
            has_tool_use = any(isinstance(b, ToolUseBlock) for b in ev.blocks)
            return TurnComplete(
                stop_reason="tool_use" if has_tool_use else "end_turn",
                usage={
                    "input_tokens": ev.usage.input_tokens,
                    "output_tokens": ev.usage.output_tokens,
                    "cache_read_tokens": ev.usage.cache_read_tokens,
                    "cache_write_tokens": ev.usage.cache_write_tokens,
                },
            )

        if isinstance(ev, ErrorEvent):
            return Error(code="engine", message=ev.message)

        if isinstance(ev, CompactionDoneEvent):
            # ``run_session`` compacted its *own* internal transcript; our
            # mirror is still the pre-compaction shape, so the next ``send``
            # would resume from stale history (#41). Re-apply the same
            # deterministic compaction to ``self._transcript`` using the
            # engine's compaction settings so resume starts from the
            # compacted shape the model actually saw.
            self._apply_compaction()
            return Compacted(
                removed_messages=ev.removed_messages,
                summary_tokens=ev.freed_tokens,
            )

        if isinstance(ev, CompactProgressEvent | StatusEvent):
            # Informational; not surfaced publicly. Slice E will route
            # the real ``ContextCompactionCompleted`` to ``Compacted``.
            return None

        # Orchestration events (TransitionEvent / TurnRecord / SessionEnd)
        # plus any unknown internal type: drop silently.
        return None

    def _apply_compaction(self) -> None:
        """Bring ``self._transcript`` to the compacted shape (#41).

        ``run_session`` compacts an internal copy of the transcript; the
        ``CompactionDoneEvent`` carries only deltas, not the compacted
        messages. Microcompaction is a pure function of the input, so we
        reproduce the same shape by re-running the engine's compactor over
        our mirror with ``force=True`` (the engine already consumed this
        turn's cooldown). A no-op when no engine / compactor is bound.
        """
        engine = self._engine
        if engine is None or engine.compactor is None:
            return
        compactor = engine.compactor
        # Local import keeps the orchestrator out of the module import graph
        # for engine-less sessions (e.g. the unit-level _translate test).
        from dream.services.compact._orchestrator import auto_compact_if_needed

        new_transcript, result = auto_compact_if_needed(
            self._transcript,
            capabilities=engine.compaction_capabilities,
            state=compactor,
            trigger="manual",
            threshold=engine.compaction_threshold,
            preserve_recent=engine.compaction_preserve_recent,
            force=True,
        )
        if result is not None:
            self._transcript[:] = new_transcript

    async def cancel(self) -> None:
        """Cancel the in-flight ``send``, if any.

        Sets a cancel signal the ``send`` loop watches between event
        awaits; the in-flight ``__anext__`` is cancelled and the inner
        ``run_session`` stream is closed in ``send``'s finally block.
        A no-op when no ``send`` is in flight.
        """
        cancel_event = self._cancel_event
        if cancel_event is None:
            return
        cancel_event.set()

    async def close(self) -> None:
        """Release resources held by this session. Idempotent.

        After ``close`` any subsequent ``send`` raises ``RuntimeError``.
        """
        if self._closed:
            return
        await self.cancel()
        self._closed = True


__all__ = ["Session", "SessionCost", "SessionOptions"]
