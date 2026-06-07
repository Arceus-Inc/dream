"""Tracer — span nesting over the trace sink (Spec 12a).

The current span lives in a module-level ``contextvars.ContextVar``, so nesting
is automatic and async-safe: concurrent tasks each see their own current span
(contextvars are per-execution-context), so two sub-agents tracing at once never
interleave. ``span()`` is for nestable events (``llm.call``/``tool.call``) and
emits one line on close (with duration); ``event()`` is for point events
(``tool.result``/``state.transition``/``validator.finding``), parented to the
current span. ``NoopTracer`` is the default so untraced runs are unchanged.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from dream.observability._events import TraceEvent, TraceEventType
from dream.observability._writer import TraceWriter
from dream.utils.clock import Clock, SystemClock

# Module-level so it is per-execution-context (async-safe) and never leaks the
# way a per-instance ContextVar would.
_CURRENT_SPAN: ContextVar[str | None] = ContextVar("dream_trace_current_span", default=None)


class Span:
    """A live span handle; callers may enrich its closing attributes."""

    def __init__(self, span_id: str) -> None:
        self.span_id = span_id
        self._extra: dict[str, object] = {}

    def set(self, key: str, value: object) -> None:
        self._extra[key] = value

    def update(self, attributes: Mapping[str, object]) -> None:
        self._extra.update(attributes)

    def end_attributes(self) -> dict[str, object]:
        return dict(self._extra)


@runtime_checkable
class Tracer(Protocol):
    """Emit OTel-shaped trace events with automatic span nesting."""

    def span(
        self, event_type: TraceEventType, attributes: Mapping[str, object] | None = None
    ) -> AbstractContextManager[Span]: ...

    def event(self, event_type: TraceEventType, attributes: Mapping[str, object]) -> None: ...


class NoopTracer:
    """A tracer that records nothing (the default when tracing is off)."""

    @contextmanager
    def span(
        self, event_type: TraceEventType, attributes: Mapping[str, object] | None = None
    ) -> Iterator[Span]:
        del event_type, attributes
        yield Span("noop")

    def event(self, event_type: TraceEventType, attributes: Mapping[str, object]) -> None:
        del event_type, attributes


class JsonlTracer:
    """Write trace events to a :class:`TraceWriter` with contextvars span nesting."""

    def __init__(
        self,
        writer: TraceWriter,
        *,
        session_id: str,
        task_id: str | None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._writer = writer
        self._session_id = session_id
        self._task_id = task_id
        self._clock: Clock = clock or SystemClock()
        self._id_factory = id_factory or _default_span_id

    @contextmanager
    def span(
        self, event_type: TraceEventType, attributes: Mapping[str, object] | None = None
    ) -> Iterator[Span]:
        parent = _CURRENT_SPAN.get()
        handle = Span(self._id_factory())
        token = _CURRENT_SPAN.set(handle.span_id)
        start_ms = self._clock.now_ms()
        try:
            yield handle
        finally:
            # ``reset(token)`` requires the same Context the ``set`` ran in.
            # Async generators driven across ``asyncio.ensure_future`` /
            # ``create_task`` boundaries (e.g. ``Session.send``'s per-anext
            # task) resume in a *different* Context, so ``reset`` would raise
            # ``ValueError`` and crash the act-loop. Fall back to ``set(parent)``
            # so nesting is still restored without coupling to token identity.
            try:
                _CURRENT_SPAN.reset(token)
            except ValueError:
                _CURRENT_SPAN.set(parent)
            attrs: dict[str, object] = {
                **(attributes or {}),
                **handle.end_attributes(),
                "duration_ms": self._clock.now_ms() - start_ms,
            }
            self._emit(event_type, handle.span_id, parent, attrs)

    def event(self, event_type: TraceEventType, attributes: Mapping[str, object]) -> None:
        self._emit(event_type, self._id_factory(), _CURRENT_SPAN.get(), dict(attributes))

    def _emit(
        self,
        event_type: TraceEventType,
        span_id: str,
        parent_span_id: str | None,
        attributes: Mapping[str, object],
    ) -> None:
        self._writer.write(
            TraceEvent(
                ts=self._now_iso(),
                session_id=self._session_id,
                task_id=self._task_id,
                event_type=event_type,
                span_id=span_id,
                parent_span_id=parent_span_id,
                attributes=attributes,
            )
        )

    def _now_iso(self) -> str:
        return datetime.fromtimestamp(self._clock.now_ms() / 1000, tz=UTC).isoformat()


def _default_span_id() -> str:
    return uuid.uuid4().hex[:16]


__all__ = ["JsonlTracer", "NoopTracer", "Span", "Tracer"]
