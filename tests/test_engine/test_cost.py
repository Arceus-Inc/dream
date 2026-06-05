"""Spec 03 stage 2 — ``UsageSnapshot`` + ``CostTracker``.

Cost is accumulated from the act-loop's ``AssistantTurnComplete`` events —
*not* extracted by parsing model output. ``UsageSnapshot`` is the per-turn
token tally; ``CostTracker`` sums them and exposes per-turn history so the
turn record (Spec 03 acceptance #8) can carry the right values.

The tracker is intentionally minimal: it sums what it's told. Pricing,
budgeting, and forecasting are out of scope here — they consume this
stream in a later layer.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from dream.engine._cost import CostTracker, UsageSnapshot

# --- UsageSnapshot -----------------------------------------------------------


def test_usage_snapshot_defaults_to_zero() -> None:
    u = UsageSnapshot()
    assert u.input_tokens == 0
    assert u.output_tokens == 0
    assert u.cache_read_tokens == 0
    assert u.cache_write_tokens == 0


def test_usage_snapshot_carries_all_fields() -> None:
    u = UsageSnapshot(
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=5,
        cache_write_tokens=3,
    )
    assert u.input_tokens == 10
    assert u.output_tokens == 20
    assert u.cache_read_tokens == 5
    assert u.cache_write_tokens == 3


def test_usage_snapshot_is_frozen() -> None:
    u = UsageSnapshot()
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        setattr(u, "input_tokens", 99)


def test_usage_snapshot_addition_combines_fields() -> None:
    a = UsageSnapshot(input_tokens=1, output_tokens=2, cache_read_tokens=3, cache_write_tokens=4)
    b = UsageSnapshot(input_tokens=10, output_tokens=20, cache_read_tokens=30, cache_write_tokens=40)
    total = a + b
    assert total == UsageSnapshot(
        input_tokens=11,
        output_tokens=22,
        cache_read_tokens=33,
        cache_write_tokens=44,
    )


def test_usage_snapshot_addition_does_not_mutate_operands() -> None:
    a = UsageSnapshot(input_tokens=1, output_tokens=2)
    b = UsageSnapshot(input_tokens=10, output_tokens=20)
    _ = a + b
    assert a == UsageSnapshot(input_tokens=1, output_tokens=2)
    assert b == UsageSnapshot(input_tokens=10, output_tokens=20)


def test_usage_snapshot_total_tokens_sums_input_and_output() -> None:
    """Cache tokens are counted separately — ``total_tokens`` is the wire bill."""
    u = UsageSnapshot(input_tokens=7, output_tokens=3, cache_read_tokens=100)
    assert u.total_tokens == 10


# --- CostTracker -------------------------------------------------------------


def test_cost_tracker_starts_empty() -> None:
    t = CostTracker()
    assert t.turns == 0
    assert t.total == UsageSnapshot()
    assert t.per_turn == []


def test_cost_tracker_records_a_single_turn() -> None:
    t = CostTracker()
    t.add(UsageSnapshot(input_tokens=5, output_tokens=2))
    assert t.turns == 1
    assert t.total == UsageSnapshot(input_tokens=5, output_tokens=2)
    assert t.per_turn == [UsageSnapshot(input_tokens=5, output_tokens=2)]


def test_cost_tracker_accumulates_multiple_turns() -> None:
    t = CostTracker()
    t.add(UsageSnapshot(input_tokens=1, output_tokens=2))
    t.add(UsageSnapshot(input_tokens=10, output_tokens=20, cache_read_tokens=3))
    t.add(UsageSnapshot(input_tokens=100, output_tokens=200))
    assert t.turns == 3
    assert t.total == UsageSnapshot(
        input_tokens=111, output_tokens=222, cache_read_tokens=3
    )
    assert t.per_turn[0].input_tokens == 1
    assert t.per_turn[1].cache_read_tokens == 3
    assert t.per_turn[2].output_tokens == 200


def test_cost_tracker_per_turn_returns_copy_not_internal_list() -> None:
    """Caller can't sneak a mutation through ``per_turn``."""
    t = CostTracker()
    t.add(UsageSnapshot(input_tokens=1))
    view = t.per_turn
    view.append(UsageSnapshot(input_tokens=999))
    assert t.turns == 1
    assert t.total == UsageSnapshot(input_tokens=1)


def test_cost_tracker_total_reflects_only_added_turns() -> None:
    t = CostTracker()
    t.add(UsageSnapshot(input_tokens=10))
    snapshot_after_one = t.total
    t.add(UsageSnapshot(input_tokens=5))
    assert snapshot_after_one == UsageSnapshot(input_tokens=10)
    assert t.total == UsageSnapshot(input_tokens=15)
