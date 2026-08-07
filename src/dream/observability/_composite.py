"""CompositeTracer — fan out to JSONL + optional OTel (non-blocking each)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager

from dream.observability._events import TraceEventType
from dream.observability._tracer import Span, Tracer


class CompositeTracer:
    """Invoke every child tracer; nesting follows the first child's span ids.

    The first tracer is the *authority* for ``Span.span_id`` returned to callers
    (JSONL remains the durable substrate). Additional tracers (OTel) receive the
    same start/end lifecycle but may mint their own backend span ids.
    """

    def __init__(self, tracers: Sequence[Tracer]) -> None:
        if not tracers:
            raise ValueError("CompositeTracer requires at least one tracer")
        self._tracers = tuple(tracers)

    @contextmanager
    def span(
        self,
        event_type: TraceEventType,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[Span]:
        managers = [t.span(event_type, attributes) for t in self._tracers]
        spans: list[Span] = []
        try:
            for manager in managers:
                spans.append(manager.__enter__())
            primary = spans[0]
            yield primary
            extras = primary.end_attributes()
            if extras:
                for sibling in spans[1:]:
                    sibling.update(extras)
        except BaseException as exc:
            for manager in reversed(managers):
                manager.__exit__(type(exc), exc, exc.__traceback__)
            raise
        else:
            for manager in reversed(managers):
                manager.__exit__(None, None, None)

    def event(self, event_type: TraceEventType, attributes: Mapping[str, object]) -> None:
        for tracer in self._tracers:
            tracer.event(event_type, attributes)


__all__ = ["CompositeTracer"]
