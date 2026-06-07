"""Spec 12b — query_logs / query_metrics BaseTools over the per-session trace."""

from __future__ import annotations

from pathlib import Path

from dream.config.paths import DreamPaths
from dream.observability._events import TraceEvent
from dream.observability._writer import TraceWriter
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin import default_registry
from dream.tools.builtin.observability_query import QueryLogsTool, QueryMetricsTool


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
        ts="2026-06-07T00:00:00+00:00",
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


# --- registration -----------------------------------------------------------


def test_query_tools_registered_in_default_registry() -> None:
    names = {t.name for t in default_registry().list_tools()}
    assert {"query_logs", "query_metrics"} <= names
