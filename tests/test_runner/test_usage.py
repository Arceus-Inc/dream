"""Tests for UsageMeter and usage_from_event (piece 4 of token-metering).

Tests cover:
- usage_from_event: correct parsing and None cases
- UsageMeter: accumulation, forwarding, edge cases
"""

from __future__ import annotations

from dream.engine._cost import UsageSnapshot
from dream.runner.events import (
    RoleSessionClosed,
    TaskCompleted,
    TaskStarted,
)
from dream.runner.observe import CapturingObserver, UsageMeter, usage_from_event


def _closed(
    *,
    model: str = "gpt-x",
    usage: UsageSnapshot | None = None,
    role: str = "planner",
    session_id: str = "s",
    cost_usd: float = 0.0,
) -> RoleSessionClosed:
    return RoleSessionClosed(
        role=role,
        session_id=session_id,
        model=model,
        usage=usage if usage is not None else UsageSnapshot(),
        cost_usd=cost_usd,
    )


def test_usage_from_event_returns_none_for_non_close_kind() -> None:
    result = usage_from_event(TaskStarted(task_id="t1", intent="x"))
    assert result is None


def test_usage_from_event_returns_none_when_model_empty_string() -> None:
    result = usage_from_event(
        _closed(
            model="",
            usage=UsageSnapshot(input_tokens=1, output_tokens=2),
        )
    )
    assert result is None


def test_usage_from_event_returns_tuple_for_valid_close_event() -> None:
    event = _closed(
        model="gpt-x",
        usage=UsageSnapshot(
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=3,
            cache_write_tokens=1,
        ),
    )
    result = usage_from_event(event)
    assert result is not None
    model, usage = result
    assert model == "gpt-x"
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.cache_read_tokens == 3
    assert usage.cache_write_tokens == 1


def test_usage_from_event_defaults_missing_token_fields_to_zero() -> None:
    result = usage_from_event(_closed(model="m", usage=UsageSnapshot()))
    assert result is not None
    _, usage = result
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cache_read_tokens == 0
    assert usage.cache_write_tokens == 0


def test_usage_meter_empty_gives_empty_dict() -> None:
    meter = UsageMeter()
    assert meter.usage_by_model == {}


def test_usage_meter_accumulates_one_close_event() -> None:
    meter = UsageMeter()
    meter.on_event(
        _closed(
            model="m",
            usage=UsageSnapshot(input_tokens=10, output_tokens=5),
        )
    )
    result = meter.usage_by_model
    assert "m" in result
    assert result["m"] == UsageSnapshot(input_tokens=10, output_tokens=5)


def test_usage_meter_sums_two_closes_for_same_model() -> None:
    meter = UsageMeter()
    meter.on_event(
        _closed(
            model="gpt-x",
            usage=UsageSnapshot(input_tokens=10, output_tokens=5),
        )
    )
    meter.on_event(
        _closed(
            model="gpt-x",
            usage=UsageSnapshot(
                input_tokens=20,
                output_tokens=7,
                cache_read_tokens=3,
            ),
        )
    )
    result = meter.usage_by_model
    assert len(result) == 1
    snap = result["gpt-x"]
    assert snap.input_tokens == 30
    assert snap.output_tokens == 12
    assert snap.cache_read_tokens == 3
    assert snap.cache_write_tokens == 0


def test_usage_meter_tracks_two_different_models_separately() -> None:
    meter = UsageMeter()
    meter.on_event(
        _closed(
            model="model-a",
            usage=UsageSnapshot(input_tokens=5, output_tokens=3),
        )
    )
    meter.on_event(
        _closed(
            model="model-b",
            usage=UsageSnapshot(
                input_tokens=8,
                output_tokens=2,
                cache_read_tokens=1,
            ),
        )
    )
    result = meter.usage_by_model
    assert len(result) == 2
    assert result["model-a"].input_tokens == 5
    assert result["model-b"].input_tokens == 8


def test_usage_meter_ignores_non_close_events() -> None:
    meter = UsageMeter()
    meter.on_event(TaskStarted(task_id="t1", intent="x"))
    meter.on_event(
        TaskCompleted(task_id="t1", sprint_count=0)
    )
    assert meter.usage_by_model == {}


def test_usage_meter_ignores_close_event_with_empty_model() -> None:
    meter = UsageMeter()
    meter.on_event(
        _closed(
            model="",
            usage=UsageSnapshot(input_tokens=5, output_tokens=3),
        )
    )
    assert meter.usage_by_model == {}


def test_usage_meter_forwards_every_event_to_inner_observer() -> None:
    inner = CapturingObserver()
    meter = UsageMeter(inner=inner)

    events = [
        TaskStarted(task_id="t1", intent="x"),
        _closed(
            model="gpt-x",
            usage=UsageSnapshot(input_tokens=5, output_tokens=2),
        ),
        TaskCompleted(task_id="t1", sprint_count=0),
    ]
    for ev in events:
        meter.on_event(ev)

    assert len(inner.events) == 3
    assert inner.events[0].kind == "task.started"
    assert inner.events[1].kind == "role.session.closed"
    assert inner.events[2].kind == "task.completed"


def test_usage_meter_with_no_inner_observer_does_not_raise() -> None:
    meter = UsageMeter(inner=None)
    meter.on_event(TaskStarted(task_id="t", intent="x"))
    meter.on_event(
        _closed(
            model="m",
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        )
    )
    assert meter.usage_by_model["m"].input_tokens == 1


def test_usage_meter_usage_by_model_returns_snapshot() -> None:
    meter = UsageMeter()
    meter.on_event(
        _closed(
            model="claude",
            usage=UsageSnapshot(
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=20,
                cache_write_tokens=10,
            ),
        )
    )
    result = meter.usage_by_model
    snap = result["claude"]
    assert isinstance(snap, UsageSnapshot)
    assert snap.total_tokens == 150  # input + output
    assert snap.cache_read_tokens == 20
    assert snap.cache_write_tokens == 10
