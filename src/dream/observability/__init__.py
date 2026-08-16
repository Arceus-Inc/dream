"""Observability — OTel-shaped JSONL traces + default OTLP export (Spec 12).

One durable line per LLM call, tool call/result, validator finding, state
transition, and (later, 12d) evaluation record, with span nesting. The same
lifecycle is exported via OpenTelemetry BatchSpanProcessor by default
(endpoint ``http://localhost:4318`` unless overridden). Set
``OTEL_SDK_DISABLED=true`` to keep JSONL only. Process shutdown waits at most
five seconds for a missing collector; JSONL is unaffected.
"""

from __future__ import annotations

from dream.observability._attributes import (
    AttributeMap,
    AttributePrimitive,
    AttributeValue,
    MutableAttributeMap,
)
from dream.observability._composite import CompositeTracer
from dream.observability._event_sink import EventSink, tail_events
from dream.observability._events import (
    TraceEvent,
    TraceEventType,
    from_jsonl_line,
    llm_call_attrs,
    state_transition_attrs,
    to_jsonl_line,
    tool_call_attrs,
    tool_result_attrs,
    validator_finding_attrs,
)
from dream.observability._factory import build_session_tracer
from dream.observability._otel_config import OtelConfig, is_otel_enabled, load_otel_config
from dream.observability._query import (
    LogQuery,
    QueryError,
    parse_window,
    query_logs,
    query_metrics,
    read_events,
)
from dream.observability._run_trace import RunTrace
from dream.observability._tracer import JsonlTracer, NoopTracer, Span, Tracer
from dream.observability._writer import TraceWriter

__all__ = [
    "AttributeMap",
    "AttributePrimitive",
    "AttributeValue",
    "CompositeTracer",
    "EventSink",
    "JsonlTracer",
    "LogQuery",
    "MutableAttributeMap",
    "NoopTracer",
    "OtelConfig",
    "QueryError",
    "RunTrace",
    "Span",
    "TraceEvent",
    "TraceEventType",
    "TraceWriter",
    "Tracer",
    "build_session_tracer",
    "from_jsonl_line",
    "is_otel_enabled",
    "llm_call_attrs",
    "load_otel_config",
    "parse_window",
    "query_logs",
    "query_metrics",
    "read_events",
    "state_transition_attrs",
    "tail_events",
    "to_jsonl_line",
    "tool_call_attrs",
    "tool_result_attrs",
    "validator_finding_attrs",
]
