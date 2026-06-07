"""Spec 12b — query engine: read trace events, filter (LogQL-subset), aggregate."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dream.observability._events import TraceEvent
from dream.observability._query import (
    LogQuery,
    QueryError,
    parse_window,
    query_logs,
    query_metrics,
    read_events,
)

HOUR_MS = 3_600_000


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _ev(
    ms: int,
    event_type: str = "tool.call",
    *,
    attributes: dict[str, object] | None = None,
    span_id: str = "sp",
) -> TraceEvent:
    return TraceEvent(
        ts=_iso(ms),
        session_id="s1",
        task_id="T1",
        event_type=event_type,  # type: ignore[arg-type]
        span_id=span_id,
        parent_span_id=None,
        attributes=attributes or {},
    )


# --- read_events ------------------------------------------------------------


def test_read_events_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_events(tmp_path / "none.jsonl") == []


def test_read_events_parses_lines_and_skips_malformed(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    from dream.observability._events import to_jsonl_line

    good = to_jsonl_line(_ev(1000))
    path.write_text(
        good + "\n" + "{not json\n" + '{"event_type":"bogus"}\n' + "\n" + good + "\n",
        encoding="utf-8",
    )
    events = read_events(path)
    assert len(events) == 2  # two good lines; malformed + blank skipped


# --- parse_window -----------------------------------------------------------


def test_parse_window_none_is_none() -> None:
    assert parse_window(None, now_ms=10_000) is None


def test_parse_window_relative_units() -> None:
    now = 10 * HOUR_MS
    assert parse_window("-1h", now_ms=now) == now - HOUR_MS
    assert parse_window("-30m", now_ms=now) == now - 30 * 60_000
    assert parse_window("-45s", now_ms=now) == now - 45_000
    assert parse_window("-2d", now_ms=now) == now - 2 * 86_400_000


def test_parse_window_absolute_iso() -> None:
    assert parse_window("1970-01-01T00:00:10+00:00", now_ms=0) == 10_000


def test_parse_window_rejects_garbage() -> None:
    with pytest.raises(QueryError):
        parse_window("-1y", now_ms=0)
    with pytest.raises(QueryError):
        parse_window("not-a-time", now_ms=0)


# --- query_logs -------------------------------------------------------------


def test_query_logs_matches_envelope_label() -> None:
    events = [_ev(1000, "tool.call"), _ev(1000, "llm.call")]
    out = query_logs(events, LogQuery(labels={"event_type": "tool.call"}))
    assert [e.event_type for e in out] == ["tool.call"]


def test_query_logs_matches_attribute_label() -> None:
    events = [
        _ev(1000, "tool.call", attributes={"tool.name": "bash"}),
        _ev(1000, "tool.call", attributes={"tool.name": "git"}),
    ]
    out = query_logs(events, LogQuery(labels={"tool.name": "bash"}))
    assert [e.attributes["tool.name"] for e in out] == ["bash"]


def test_query_logs_contains_line_filter() -> None:
    events = [
        _ev(1000, "tool.call", attributes={"tool.name": "bash"}),
        _ev(1000, "tool.call", attributes={"tool.name": "git"}),
    ]
    out = query_logs(events, LogQuery(contains="git"))
    assert len(out) == 1 and out[0].attributes["tool.name"] == "git"


def test_query_logs_time_window_inclusive_bounds() -> None:
    events = [_ev(1000), _ev(2000), _ev(3000)]
    out = query_logs(events, LogQuery(since_ms=2000, until_ms=3000))
    assert [_ms(e) for e in out] == [2000, 3000]


def test_query_logs_empty_when_nothing_matches() -> None:
    assert query_logs([_ev(1000, "tool.call")], LogQuery(labels={"event_type": "llm.call"})) == []


# --- query_metrics ----------------------------------------------------------


def _token_events() -> list[TraceEvent]:
    return [
        _ev(1000, "llm.call", attributes={"gen_ai.usage.prompt_tokens": 10}),
        _ev(2000, "llm.call", attributes={"gen_ai.usage.prompt_tokens": 30}),
        _ev(3000, "tool.call", attributes={"tool.name": "bash"}),
    ]


def test_query_metrics_sum() -> None:
    val = query_metrics(
        _token_events(),
        metric="gen_ai.usage.prompt_tokens",
        agg="sum",
        since_ms=None,
        until_ms=None,
    )
    assert val == 40.0


def test_query_metrics_avg_and_max() -> None:
    events = _token_events()
    assert query_metrics(events, metric="gen_ai.usage.prompt_tokens", agg="avg",
                         since_ms=None, until_ms=None) == 20.0
    assert query_metrics(events, metric="gen_ai.usage.prompt_tokens", agg="max",
                         since_ms=None, until_ms=None) == 30.0


def test_query_metrics_count_pseudo_metric() -> None:
    val = query_metrics(
        _token_events(),
        metric="count",
        agg="sum",
        since_ms=None,
        until_ms=None,
        labels={"event_type": "llm.call"},
    )
    assert val == 2.0


def test_query_metrics_no_samples_returns_none() -> None:
    assert query_metrics(
        _token_events(), metric="nonexistent.attr", agg="sum", since_ms=None, until_ms=None
    ) is None


def test_query_metrics_rejects_unknown_agg() -> None:
    with pytest.raises(QueryError):
        query_metrics(_token_events(), metric="count", agg="median", since_ms=None, until_ms=None)


def test_query_metrics_ignores_bool_attributes() -> None:
    events = [_ev(1000, "tool.result", attributes={"tool.is_error": True})]
    # booleans must not be summed as 1 — they are not numeric metrics.
    assert query_metrics(events, metric="tool.is_error", agg="sum",
                         since_ms=None, until_ms=None) is None


def _ms(event: TraceEvent) -> int:
    return int(datetime.fromisoformat(event.ts).timestamp() * 1000)
