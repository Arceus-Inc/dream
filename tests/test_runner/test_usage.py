"""Tests for UsageMeter and usage_from_event (piece 4 of token-metering).

Tests cover:
- usage_from_event: correct parsing and None cases
- UsageMeter: accumulation, forwarding, edge cases
"""

from __future__ import annotations

from typing import Any

from dream.engine._cost import UsageSnapshot

# ---------------------------------------------------------------------------
# usage_from_event
# ---------------------------------------------------------------------------


def test_usage_from_event_returns_none_for_non_close_kind() -> None:
    from dream.runner._usage import usage_from_event

    result = usage_from_event({"kind": "task.started", "task_id": "t1"})
    assert result is None


def test_usage_from_event_returns_none_for_missing_kind() -> None:
    from dream.runner._usage import usage_from_event

    result = usage_from_event({"task_id": "t1"})
    assert result is None


def test_usage_from_event_returns_none_when_model_missing() -> None:
    from dream.runner._usage import usage_from_event

    result = usage_from_event(
        {
            "kind": "role.session.closed",
            "usage": {"input_tokens": 1, "output_tokens": 2,
                      "cache_read_tokens": 0, "cache_write_tokens": 0},
        }
    )
    assert result is None


def test_usage_from_event_returns_none_when_model_empty_string() -> None:
    from dream.runner._usage import usage_from_event

    result = usage_from_event(
        {
            "kind": "role.session.closed",
            "model": "",
            "usage": {"input_tokens": 1, "output_tokens": 2,
                      "cache_read_tokens": 0, "cache_write_tokens": 0},
        }
    )
    assert result is None


def test_usage_from_event_returns_none_when_usage_not_a_mapping() -> None:
    from dream.runner._usage import usage_from_event

    result = usage_from_event(
        {
            "kind": "role.session.closed",
            "model": "gpt-x",
            "usage": "not-a-dict",
        }
    )
    assert result is None


def test_usage_from_event_returns_none_when_usage_missing() -> None:
    from dream.runner._usage import usage_from_event

    result = usage_from_event(
        {
            "kind": "role.session.closed",
            "model": "gpt-x",
        }
    )
    assert result is None


def test_usage_from_event_returns_tuple_for_valid_close_event() -> None:
    from dream.runner._usage import usage_from_event

    event = {
        "kind": "role.session.closed",
        "model": "gpt-x",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 3,
            "cache_write_tokens": 1,
        },
    }
    result = usage_from_event(event)
    assert result is not None
    model, usage = result
    assert model == "gpt-x"
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.cache_read_tokens == 3
    assert usage.cache_write_tokens == 1


def test_usage_from_event_defaults_missing_token_fields_to_zero() -> None:
    from dream.runner._usage import usage_from_event

    event = {
        "kind": "role.session.closed",
        "model": "m",
        "usage": {},  # empty mapping
    }
    result = usage_from_event(event)
    assert result is not None
    _, usage = result
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cache_read_tokens == 0
    assert usage.cache_write_tokens == 0


# ---------------------------------------------------------------------------
# UsageMeter
# ---------------------------------------------------------------------------


def test_usage_meter_empty_gives_empty_dict() -> None:
    from dream.runner._usage import UsageMeter

    meter = UsageMeter()
    assert meter.usage_by_model == {}


def test_usage_meter_accumulates_one_close_event() -> None:
    from dream.runner._usage import UsageMeter

    meter = UsageMeter()
    meter.on_event(
        {
            "kind": "role.session.closed",
            "model": "m",
            "usage": {"input_tokens": 10, "output_tokens": 5,
                      "cache_read_tokens": 0, "cache_write_tokens": 0},
        }
    )
    result = meter.usage_by_model
    assert "m" in result
    assert result["m"] == UsageSnapshot(input_tokens=10, output_tokens=5)


def test_usage_meter_sums_two_closes_for_same_model() -> None:
    from dream.runner._usage import UsageMeter

    meter = UsageMeter()
    meter.on_event(
        {
            "kind": "role.session.closed",
            "model": "gpt-x",
            "usage": {"input_tokens": 10, "output_tokens": 5,
                      "cache_read_tokens": 0, "cache_write_tokens": 0},
        }
    )
    meter.on_event(
        {
            "kind": "role.session.closed",
            "model": "gpt-x",
            "usage": {"input_tokens": 20, "output_tokens": 7,
                      "cache_read_tokens": 3, "cache_write_tokens": 0},
        }
    )
    result = meter.usage_by_model
    assert len(result) == 1
    snap = result["gpt-x"]
    assert snap.input_tokens == 30
    assert snap.output_tokens == 12
    assert snap.cache_read_tokens == 3
    assert snap.cache_write_tokens == 0


def test_usage_meter_tracks_two_different_models_separately() -> None:
    from dream.runner._usage import UsageMeter

    meter = UsageMeter()
    meter.on_event(
        {
            "kind": "role.session.closed",
            "model": "model-a",
            "usage": {"input_tokens": 5, "output_tokens": 3,
                      "cache_read_tokens": 0, "cache_write_tokens": 0},
        }
    )
    meter.on_event(
        {
            "kind": "role.session.closed",
            "model": "model-b",
            "usage": {"input_tokens": 8, "output_tokens": 2,
                      "cache_read_tokens": 1, "cache_write_tokens": 0},
        }
    )
    result = meter.usage_by_model
    assert len(result) == 2
    assert result["model-a"].input_tokens == 5
    assert result["model-b"].input_tokens == 8


def test_usage_meter_ignores_non_close_events() -> None:
    from dream.runner._usage import UsageMeter

    meter = UsageMeter()
    meter.on_event({"kind": "task.started", "task_id": "t1"})
    meter.on_event({"kind": "planner.completed"})
    assert meter.usage_by_model == {}


def test_usage_meter_ignores_close_event_with_empty_model() -> None:
    from dream.runner._usage import UsageMeter

    meter = UsageMeter()
    meter.on_event(
        {
            "kind": "role.session.closed",
            "model": "",
            "usage": {"input_tokens": 5, "output_tokens": 3,
                      "cache_read_tokens": 0, "cache_write_tokens": 0},
        }
    )
    assert meter.usage_by_model == {}


def test_usage_meter_ignores_close_event_with_non_mapping_usage() -> None:
    from dream.runner._usage import UsageMeter

    meter = UsageMeter()
    meter.on_event(
        {
            "kind": "role.session.closed",
            "model": "gpt-x",
            "usage": "bad",
        }
    )
    assert meter.usage_by_model == {}


def test_usage_meter_forwards_every_event_to_inner_observer() -> None:
    from dream.runner._observer import _CapturingObserver
    from dream.runner._usage import UsageMeter

    inner = _CapturingObserver()
    meter = UsageMeter(inner=inner)

    events: list[dict[str, Any]] = [
        {"kind": "task.started", "task_id": "t1"},
        {
            "kind": "role.session.closed",
            "model": "gpt-x",
            "usage": {"input_tokens": 5, "output_tokens": 2,
                      "cache_read_tokens": 0, "cache_write_tokens": 0},
        },
        {"kind": "task.completed", "task_id": "t1"},
    ]
    for ev in events:
        meter.on_event(ev)

    # All events forwarded
    assert len(inner.events) == 3
    assert inner.events[0]["kind"] == "task.started"
    assert inner.events[1]["kind"] == "role.session.closed"
    assert inner.events[2]["kind"] == "task.completed"


def test_usage_meter_with_no_inner_observer_does_not_raise() -> None:
    from dream.runner._usage import UsageMeter

    meter = UsageMeter(inner=None)
    meter.on_event({"kind": "task.started"})
    meter.on_event(
        {
            "kind": "role.session.closed",
            "model": "m",
            "usage": {"input_tokens": 1, "output_tokens": 1,
                      "cache_read_tokens": 0, "cache_write_tokens": 0},
        }
    )
    assert meter.usage_by_model["m"].input_tokens == 1


def test_usage_meter_usage_by_model_returns_snapshot() -> None:
    from dream.runner._usage import UsageMeter

    meter = UsageMeter()
    meter.on_event(
        {
            "kind": "role.session.closed",
            "model": "claude",
            "usage": {"input_tokens": 100, "output_tokens": 50,
                      "cache_read_tokens": 20, "cache_write_tokens": 10},
        }
    )
    result = meter.usage_by_model
    snap = result["claude"]
    assert isinstance(snap, UsageSnapshot)
    assert snap.total_tokens == 150  # input + output
    assert snap.cache_read_tokens == 20
    assert snap.cache_write_tokens == 10
