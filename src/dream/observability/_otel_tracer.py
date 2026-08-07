"""OtelTracer — real OpenTelemetry spans behind the dream Tracer protocol."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import cast

from opentelemetry import trace
from opentelemetry.trace import Span as SdkSpan
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace import Tracer as SdkTracer
from opentelemetry.util.types import Attributes

from dream.observability._attributes import MutableAttributeMap, filter_attribute_map
from dream.observability._events import TraceEventType
from dream.observability._tracer import Span

_CURRENT_OTEL_SPAN: ContextVar[SdkSpan | None] = ContextVar(
    "dream_otel_current_span",
    default=None,
)


class OtelTracer:
    """Emit real OTel spans with the same nesting contract as :class:`JsonlTracer`."""

    def __init__(
        self,
        tracer: SdkTracer,
        *,
        session_id: str,
        task_id: str | None,
    ) -> None:
        self._tracer = tracer
        self._session_id = session_id
        self._task_id = task_id

    @contextmanager
    def span(
        self,
        event_type: TraceEventType,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[Span]:
        base = self._base_attrs(attributes)
        parent = _CURRENT_OTEL_SPAN.get()
        ctx = trace.set_span_in_context(parent) if parent is not None else None
        otel_span = self._tracer.start_span(
            event_type,
            context=ctx,
            attributes=_as_otel_attributes(base),
        )
        handle = Span(format(otel_span.get_span_context().span_id, "016x"))
        token = _CURRENT_OTEL_SPAN.set(otel_span)
        try:
            yield handle
        except Exception as exc:
            otel_span.set_status(Status(StatusCode.ERROR, str(exc)))
            otel_span.record_exception(exc)
            raise
        finally:
            extras = filter_attribute_map(handle.end_attributes())
            if extras:
                attrs = _as_otel_attributes(extras)
                if attrs is not None:
                    otel_span.set_attributes(attrs)
            otel_span.end()
            try:
                _CURRENT_OTEL_SPAN.reset(token)
            except ValueError:
                _CURRENT_OTEL_SPAN.set(parent)

    def event(self, event_type: TraceEventType, attributes: Mapping[str, object]) -> None:
        base = self._base_attrs(attributes)
        parent = _CURRENT_OTEL_SPAN.get()
        ctx = trace.set_span_in_context(parent) if parent is not None else None
        otel_span = self._tracer.start_span(
            event_type,
            context=ctx,
            attributes=_as_otel_attributes(base),
        )
        otel_span.end()

    def _base_attrs(self, attributes: Mapping[str, object] | None) -> MutableAttributeMap:
        base = filter_attribute_map(attributes)
        base.setdefault("dream.session_id", self._session_id)
        if self._task_id is not None:
            base.setdefault("dream.task_id", self._task_id)
        return base


def _as_otel_attributes(attributes: MutableAttributeMap) -> Attributes:
    # SDK stubs are narrower than the runtime AttributeValue union; values are
    # already filtered to OTel-legal primitives in ``filter_attribute_map``.
    return cast(Attributes, attributes)


__all__ = ["OtelTracer"]
