"""Spec 12b — query_logs / query_metrics BaseTools over the per-session trace."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dream.config.paths import DreamPaths
from dream.observability._events import TraceEvent
from dream.observability._writer import TraceWriter
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin import default_registry
from dream.tools.builtin.observability_query import QueryLogsTool, QueryMetricsTool
from dream.utils.clock import FakeClock

_EVENT_TS = "2026-06-07T00:00:00+00:00"
_EVENT_MS = int(datetime.fromisoformat(_EVENT_TS).timestamp() * 1000)


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s1")


def _seed(tmp_path: Path) -> None:
    path = DreamPaths.resolve(tmp_path).trace_log("s1")
    writer = TraceWriter(path)
    writer.write(_ev("llm.call", {"gen_ai.usage.prompt_tokens": 10}))
    writer.write(_ev("tool.call", {"tool.name": "bash"}))
    writer.write(_ev("llm.call", {"gen_ai.usage.prompt_tokens": 30}))


def _ev(event_type: str, attributes: dict[str, object]) -> TraceEvent:
    return TraceEvent(
        ts=_EVENT_TS,
        session_id="s1",
        task_id="T1",
        event_type=event_type,  # type: ignore[arg-type]
        span_id="sp",
        parent_span_id=None,
        attributes=attributes,
    )


# --- query_logs -------------------------------------------------------------


def test_query_logs_is_read_only() -> None:
    assert QueryLogsTool().is_read_only() is True


async def test_query_logs_filters_by_label(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await QueryLogsTool().execute({"labels": {"event_type": "tool.call"}}, _ctx(tmp_path))
    assert result.is_error is False
    assert result.content.count("\n") == 0  # exactly one matching line
    assert "tool.call" in result.content and "llm.call" not in result.content


async def test_query_logs_empty_when_no_trace(tmp_path: Path) -> None:
    result = await QueryLogsTool().execute({}, _ctx(tmp_path))
    assert result.is_error is False
    assert "no matching events" in result.content


async def test_query_logs_bad_time_spec_is_tool_error(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await QueryLogsTool().execute({"since": "-1y"}, _ctx(tmp_path))
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_query_logs_defaults_until_to_now(tmp_path: Path) -> None:
    """Omitting ``until`` must bound the window at *now*, not leave it open —
    so future-dated events (clock skew, replayed traces) are excluded."""
    _seed(tmp_path)
    # ``now`` is one hour *before* the seeded events, which are therefore future.
    clock = FakeClock(start_ms=_EVENT_MS - 3_600_000)
    result = await QueryLogsTool(clock=clock).execute({}, _ctx(tmp_path))
    assert result.is_error is False
    assert "no matching events" in result.content


# --- query_metrics ----------------------------------------------------------


def test_query_metrics_is_read_only() -> None:
    assert QueryMetricsTool().is_read_only() is True


async def test_query_metrics_sum_tokens(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await QueryMetricsTool().execute(
        {"metric": "gen_ai.usage.prompt_tokens", "agg": "sum"}, _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "40" in result.content


async def test_query_metrics_count(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await QueryMetricsTool().execute(
        {"metric": "count", "labels": {"event_type": "llm.call"}}, _ctx(tmp_path)
    )
    assert "2" in result.content


async def test_query_metrics_no_samples(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = await QueryMetricsTool().execute(
        {"metric": "nope.attr", "agg": "sum"}, _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "no samples" in result.content


async def test_query_metrics_defaults_until_to_now(tmp_path: Path) -> None:
    """Same default-window contract for metrics: future-dated samples must not
    leak into an aggregation when ``until`` is omitted."""
    _seed(tmp_path)
    clock = FakeClock(start_ms=_EVENT_MS - 3_600_000)
    result = await QueryMetricsTool(clock=clock).execute(
        {"metric": "gen_ai.usage.prompt_tokens", "agg": "sum"}, _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "no samples" in result.content


# --- registration -----------------------------------------------------------


def test_query_tools_registered_via_observability_pack() -> None:
    from dream.tools.builtin import register_observability_tools

    reg = default_registry()
    assert {"query_logs", "query_metrics"}.isdisjoint({t.name for t in reg.list_tools()})
    register_observability_tools(reg)
    names = {t.name for t in reg.list_tools()}
    assert {"query_logs", "query_metrics"} <= names
