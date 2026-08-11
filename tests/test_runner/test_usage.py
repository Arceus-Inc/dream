"""Tests for UsageMeter (token-metering)."""

from __future__ import annotations

from dream.engine._cost import UsageSnapshot
from dream.runner.events import (
    RoleSessionClosed,
    TaskCompleted,
    TaskStarted,
)
from dream.runner.observe import CapturingObserver, UsageMeter


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


def test_usage_meter_empty_gives_empty_dict() -> None:
    meter = UsageMeter()
    assert meter.usage_by_model == {}


def test_usage_meter_ignores_non_close_events() -> None:
    meter = UsageMeter()
    meter.on_event(TaskStarted(task_id="t1", intent="x"))
    assert meter.usage_by_model == {}


def test_usage_meter_ignores_empty_model() -> None:
    meter = UsageMeter()
    meter.on_event(
        _closed(
            model="",
            usage=UsageSnapshot(input_tokens=1, output_tokens=2),
        )
    )
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
    meter.on_event(_closed(model="m", usage=UsageSnapshot(input_tokens=10, output_tokens=5)))
    meter.on_event(_closed(model="m", usage=UsageSnapshot(input_tokens=3, output_tokens=2)))
    assert meter.usage_by_model["m"] == UsageSnapshot(input_tokens=13, output_tokens=7)


def test_usage_meter_tracks_models_separately() -> None:
    meter = UsageMeter()
    meter.on_event(_closed(model="a", usage=UsageSnapshot(input_tokens=1, output_tokens=1)))
    meter.on_event(_closed(model="b", usage=UsageSnapshot(input_tokens=2, output_tokens=2)))
    assert set(meter.usage_by_model) == {"a", "b"}
    assert meter.usage_by_model["a"].input_tokens == 1
    assert meter.usage_by_model["b"].input_tokens == 2


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
    assert isinstance(inner.events[0], TaskStarted)
    assert isinstance(inner.events[1], RoleSessionClosed)
    assert isinstance(inner.events[2], TaskCompleted)


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
