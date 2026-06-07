"""Spec 08 — injectable Clock seam (wall-clock + deterministic FakeClock)."""

from __future__ import annotations

import time

import pytest

from dream.utils.clock import Clock, FakeClock, SystemClock


def test_system_clock_returns_epoch_millis() -> None:
    before = int(time.time() * 1000)
    now = SystemClock().now_ms()
    after = int(time.time() * 1000)
    assert before <= now <= after


def test_system_clock_is_non_decreasing() -> None:
    clock = SystemClock()
    first = clock.now_ms()
    second = clock.now_ms()
    assert second >= first


def test_fake_clock_starts_at_given_instant() -> None:
    assert FakeClock(start_ms=1000).now_ms() == 1000


def test_fake_clock_default_start_is_zero() -> None:
    assert FakeClock().now_ms() == 0


def test_fake_clock_is_stable_without_advance() -> None:
    clock = FakeClock(start_ms=500)
    assert clock.now_ms() == clock.now_ms() == 500


def test_fake_clock_advance_moves_time_forward() -> None:
    clock = FakeClock(start_ms=1000)
    clock.advance(250)
    assert clock.now_ms() == 1250


def test_fake_clock_rejects_negative_advance() -> None:
    clock = FakeClock()
    with pytest.raises(ValueError):
        clock.advance(-1)


def test_both_clocks_satisfy_protocol() -> None:
    clocks: list[Clock] = [SystemClock(), FakeClock()]
    for clock in clocks:
        assert isinstance(clock.now_ms(), int)
