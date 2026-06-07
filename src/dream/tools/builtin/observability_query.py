"""``query_logs`` / ``query_metrics`` — agent-queryable observability (Spec 12b).

Both are read-only tools over the current session's trace
(``.dream/sidecars/{session}/logs/trace.jsonl``, written by 12a). They resolve
that path from the execution context, so nothing needs injecting. Large results
ride the engine dispatcher's existing #04 offload of ``ToolResult.content`` — the
tools just return the text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from dream.config.paths import DreamPaths
from dream.contracts.tool import ToolResult
from dream.observability._events import to_jsonl_line
from dream.observability._query import (
    LogQuery,
    QueryError,
    parse_window,
    query_logs,
    query_metrics,
    read_events,
)
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.utils.clock import Clock, SystemClock

_READ_ONLY = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=10.0)


class QueryLogsInput(BaseModel):
    """Arguments for ``query_logs`` (a LogQL-subset)."""

    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Exact-match matchers over envelope fields (event_type, "
        "session_id, task_id, span_id, parent_span_id) and attribute keys.",
    )
    contains: str | None = Field(default=None, description="Substring line filter.")
    since: str | None = Field(default=None, description="Relative '-1h' or ISO-8601 start.")
    until: str | None = Field(default=None, description="Relative or ISO-8601 end (default: now).")


class QueryMetricsInput(BaseModel):
    """Arguments for ``query_metrics`` (a PromQL-subset)."""

    metric: str = Field(description="Numeric attribute key, or 'count' to count events.")
    agg: Literal["sum", "avg", "max"] = Field(default="sum", description="Aggregation.")
    labels: dict[str, str] = Field(default_factory=dict, description="Matchers to narrow the set.")
    since: str | None = Field(default=None, description="Relative '-1h' or ISO-8601 start.")
    until: str | None = Field(default=None, description="Relative or ISO-8601 end (default: now).")


class QueryLogsTool(BaseTool):
    """Query the session's trace events (LogQL-subset)."""

    name = "query_logs"
    description = "Query this session's trace events by labels, substring, and time window."
    declaration = _READ_ONLY
    input_model = QueryLogsInput

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = QueryLogsInput.model_validate(input)
        now = self._clock.now_ms()
        try:
            query = LogQuery(
                labels=args.labels,
                contains=args.contains,
                since_ms=parse_window(args.since, now_ms=now),
                until_ms=parse_window(args.until, now_ms=now),
            )
        except QueryError as exc:
            return _bad_query(exc)

        matched = query_logs(read_events(_trace_path(ctx)), query)
        if not matched:
            return ToolResult(content="(no matching events)", metadata={"count": 0})
        body = "\n".join(to_jsonl_line(event) for event in matched)
        return ToolResult(content=body, metadata={"count": len(matched)})


class QueryMetricsTool(BaseTool):
    """Aggregate a numeric trace attribute, or count events (PromQL-subset)."""

    name = "query_metrics"
    description = "Aggregate a numeric trace attribute (sum/avg/max) or count events."
    declaration = _READ_ONLY
    input_model = QueryMetricsInput

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        args = QueryMetricsInput.model_validate(input)
        now = self._clock.now_ms()
        try:
            value = query_metrics(
                read_events(_trace_path(ctx)),
                metric=args.metric,
                agg=args.agg,
                since_ms=parse_window(args.since, now_ms=now),
                until_ms=parse_window(args.until, now_ms=now),
                labels=args.labels,
            )
        except QueryError as exc:
            return _bad_query(exc)

        if value is None:
            return ToolResult(content="(no samples)", metadata={"metric": args.metric})
        if args.metric == "count":
            content = f"count = {value:g}"
        else:
            content = f"{args.metric} {args.agg} = {value:g}"
        return ToolResult(content=content, metadata={"metric": args.metric, "value": value})


def _trace_path(ctx: ToolExecutionContext) -> Path:
    return DreamPaths.resolve(ctx.working_dir).trace_log(ctx.session_id)


def _bad_query(exc: QueryError) -> ToolResult:
    return ToolResult(
        content=f"Invalid query: {exc}",
        is_error=True,
        metadata={
            "root_cause": str(exc),
            "safe_retry": "fix the time spec / aggregation and retry",
            "stop_condition": "do not retry the same malformed query",
        },
    )


__all__ = [
    "QueryLogsInput",
    "QueryLogsTool",
    "QueryMetricsInput",
    "QueryMetricsTool",
]
