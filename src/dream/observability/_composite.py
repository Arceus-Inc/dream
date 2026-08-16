"""CompositeTracer — fan out to JSONL + optional OTel (non-blocking each)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, suppress

from dream.observability._events import TraceEventType
from dream.observability._tracer import Span, Tracer


class CompositeTracer:
    """Invoke every child tracer; nesting follows the first child's span ids.

    The first tracer is the *authority* for ``Span.span_id`` returned to callers
    (JSONL remains the durable substrate). Additional tracers (OTel) receive the
    same start/end lifecycle but may mint their own backend span ids.

    If a later child raises during ``__enter__``, only managers that successfully
    entered are exited — never ``__exit__`` a span that did not ``__enter__``.

    Primary ``span.set`` / ``span.update`` extras are copied onto sibling handles
    in a ``finally`` around ``yield`` (success, exception, cancellation, and
    generator close) *before* ``ExitStack`` exits those siblings.

    Sibling ``event()`` failures are isolated: the additive OTLP sink must not
    abort the caller after durable JSONL has already recorded.
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
        with ExitStack() as stack:
            spans = [
                stack.enter_context(tracer.span(event_type, attributes))
                for tracer in self._tracers
            ]
            primary = spans[0]
            try:
                yield primary
            finally:
                extras = primary.end_attributes()
                if extras:
                    for sibling in spans[1:]:
                        with suppress(Exception):
                            sibling.update(extras)

    def event(self, event_type: TraceEventType, attributes: Mapping[str, object]) -> None:
        primary, *siblings = self._tracers
        primary.event(event_type, attributes)
        for tracer in siblings:
            with suppress(Exception):
                tracer.event(event_type, attributes)


__all__ = ["CompositeTracer"]
