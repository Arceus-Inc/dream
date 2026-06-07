"""Query the OTel trace — LogQL/PromQL subsets over ``trace.jsonl`` (Spec 12b).

Pure functions over the trace events 12a produced: ``query_logs`` filters by a
time window + exact-match label matchers (envelope fields *and* attribute keys)
+ a substring line-filter; ``query_metrics`` aggregates a numeric attribute (or
counts events) with ``sum``/``avg``/``max``. No separate metrics store — metrics
are derived from the trace. Reads tolerate a partially-flushed tail line.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dream.observability._events import TraceEvent, from_jsonl_line, to_jsonl_line

_AGGREGATIONS = ("sum", "avg", "max")
_DURATION_UNITS_MS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
_COUNT_METRIC = "count"
_ENVELOPE_LABELS = ("event_type", "session_id", "task_id", "span_id", "parent_span_id")


class QueryError(ValueError):
    """Raised for a malformed time spec or unsupported aggregation."""


@dataclass(frozen=True)
class LogQuery:
    """A LogQL-subset query: label matchers + line filter + time window."""

    labels: Mapping[str, str] = field(default_factory=dict)
    contains: str | None = None
    since_ms: int | None = None
    until_ms: int | None = None


def read_events(path: Path) -> list[TraceEvent]:
    """Parse ``trace.jsonl``; a missing file is empty, malformed lines are skipped."""
    if not path.is_file():
        return []
    events: list[TraceEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            events.append(from_jsonl_line(stripped))
        except (ValueError, KeyError):
            continue  # malformed or partially-flushed tail line — skip, never crash
    return events


def parse_window(spec: str | None, *, now_ms: int) -> int | None:
    """Parse a ``-1h``-style relative offset or an absolute ISO-8601 instant."""
    if spec is None:
        return None
    spec = spec.strip()
    if spec.startswith("-"):
        return now_ms - _parse_duration_ms(spec[1:])
    try:
        dt = datetime.fromisoformat(spec)
    except ValueError as exc:
        raise QueryError(f"invalid time spec: {spec!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def query_logs(events: Sequence[TraceEvent], query: LogQuery) -> list[TraceEvent]:
    """Return events matching the window, label matchers, and line filter."""
    out: list[TraceEvent] = []
    for event in events:
        ts = _event_ms(event)
        if query.since_ms is not None and ts < query.since_ms:
            continue
        if query.until_ms is not None and ts > query.until_ms:
            continue
        if not _labels_match(event, query.labels):
            continue
        if query.contains is not None and query.contains not in to_jsonl_line(event):
            continue
        out.append(event)
    return out


def query_metrics(
    events: Sequence[TraceEvent],
    *,
    metric: str,
    agg: str,
    since_ms: int | None,
    until_ms: int | None,
    labels: Mapping[str, str] | None = None,
) -> float | None:
    """Aggregate a numeric attribute (or count events) over the window+labels.

    ``metric="count"`` returns the number of matching events (always defined,
    ``agg`` ignored). Otherwise returns ``agg`` over the numeric values of the
    ``metric`` attribute, or ``None`` when no numeric samples exist.
    """
    if agg not in _AGGREGATIONS:
        raise QueryError(f"unsupported agg {agg!r}; expected one of {_AGGREGATIONS}")
    matched = query_logs(
        events, LogQuery(labels=labels or {}, since_ms=since_ms, until_ms=until_ms)
    )
    if metric == _COUNT_METRIC:
        return float(len(matched))
    values: list[float] = []
    for event in matched:
        value = event.attributes.get(metric)
        # bool is an int subclass but is never a metric sample.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        values.append(float(value))
    if not values:
        return None
    if agg == "sum":
        return sum(values)
    if agg == "avg":
        return sum(values) / len(values)
    return max(values)


def _parse_duration_ms(body: str) -> int:
    if not body or body[-1] not in _DURATION_UNITS_MS:
        raise QueryError(f"invalid duration: -{body!r}; expected a number + s/m/h/d")
    try:
        value = int(body[:-1])
    except ValueError as exc:
        raise QueryError(f"invalid duration: -{body!r}") from exc
    return value * _DURATION_UNITS_MS[body[-1]]


def _event_ms(event: TraceEvent) -> int:
    return int(datetime.fromisoformat(event.ts).timestamp() * 1000)


def _labels_match(event: TraceEvent, labels: Mapping[str, str]) -> bool:
    return all(_label_value(event, key) == want for key, want in labels.items())


def _label_value(event: TraceEvent, key: str) -> str | None:
    if key in _ENVELOPE_LABELS:
        value: object = getattr(event, key)
    else:
        value = event.attributes.get(key)
    return None if value is None else str(value)


__all__ = [
    "LogQuery",
    "QueryError",
    "parse_window",
    "query_logs",
    "query_metrics",
    "read_events",
]
