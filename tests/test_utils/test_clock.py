"""Spec 08 — injectable Clock seam (wall-clock + deterministic FakeClock)."""

from __future__ import annotations

import pytest

from dream.utils.clock import Clock, FakeClock, SystemClock


def test_system_clock_returns_epoch_millis(monkeypatch: pytest.MonkeyPatch) -> None:
    # Freeze the wall clock so the assertion can't flake on NTP/VM clock jumps.
    monkeypatch.setattr("dream.utils.clock.time.time", lambda: 1234.567)
    assert SystemClock().now_ms() == 1_234_567


def test_system_clock_reads_time_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # A controlled, non-decreasing sequence proves now_ms reflects time.time
    # without relying on the host clock being monotonic.
    ticks = iter([100.0, 100.0, 101.0])
    monkeypatch.setattr("dream.utils.clock.time.time", lambda: next(ticks))
    clock = SystemClock()
    first = clock.now_ms()
    second = clock.now_ms()
    third = clock.now_ms()
    assert first == 100_000
    assert second == first
    assert third == 101_000


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
