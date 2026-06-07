"""Observability — OTel-shaped JSONL traces of the agent run (Spec 12a).

One durable line per LLM call, tool call/result, validator finding, state
transition, and (later, 12d) evaluation record, with span nesting. The trace is
the substrate the rest of #12 (query tools, evals) and #09/#11 read from.
"""

from __future__ import annotations

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
from dream.observability._query import (
    LogQuery,
    QueryError,
    parse_window,
    query_logs,
    query_metrics,
    read_events,
)
from dream.observability._tracer import JsonlTracer, NoopTracer, Span, Tracer
from dream.observability._writer import TraceWriter

__all__ = [
    "JsonlTracer",
    "LogQuery",
    "NoopTracer",
    "QueryError",
    "Span",
    "TraceEvent",
    "TraceEventType",
    "TraceWriter",
    "Tracer",
    "from_jsonl_line",
    "llm_call_attrs",
    "parse_window",
    "query_logs",
    "query_metrics",
    "read_events",
    "state_transition_attrs",
    "to_jsonl_line",
    "tool_call_attrs",
    "tool_result_attrs",
    "validator_finding_attrs",
]
