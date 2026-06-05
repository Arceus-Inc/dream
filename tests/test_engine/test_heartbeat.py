"""Spec 03 stage 3b — heartbeat & coma detection.

Pins the heartbeat primitives:

- ``ComaDetected`` exception type marks the coma trigger.
- ``HeartbeatMonitor.run()`` polls ``health()`` every ``interval`` seconds;
  ``threshold`` consecutive failures (False return OR exception) raise
  ``ComaDetected`` (#11/#12).
- A successful ping resets the consecutive-failure counter.
- ``consecutive_failures`` is observable for assertions.
- The poll loop honours the configured interval (no busy spin).

These tests use sub-millisecond intervals so they stay fast and
deterministic. They do NOT test session-level coma handling — that
belongs in ``test_session_rituals.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from dream.engine._heartbeat import (
    ComaDetected,
    HeartbeatConfig,
    HeartbeatMonitor,
)


def _scripted_health(results: list[object]):
    """Return an async health() that yields the next scripted result per call.

    Each result is either ``bool`` (returned) or an ``Exception`` instance
    (raised). When the script is exhausted the last value repeats — this
    lets ``always_ok`` / ``always_fail`` style tests not micro-manage
    the script length.
    """
    idx = [0]

    async def health() -> bool:
        i = idx[0]
        if i < len(results):
            idx[0] = i + 1
        value = results[min(i, len(results) - 1)]
        if isinstance(value, BaseException):
            raise value
        return bool(value)

    return health


# --- ComaDetected -----------------------------------------------------------


def test_coma_detected_is_an_exception() -> None:
    assert issubclass(ComaDetected, Exception)


def test_coma_detected_carries_failure_count() -> None:
    err = ComaDetected(consecutive_failures=4)
    assert err.consecutive_failures == 4
    assert "4" in str(err)


# --- HeartbeatMonitor behaviour --------------------------------------------


async def test_monitor_with_always_healthy_health_does_not_raise_in_window() -> None:
    monitor = HeartbeatMonitor(
        health=_scripted_health([True]), interval=0.001, threshold=3
    )
    # Run a short window — should not raise.
    with pytest.raises(asyncio.TimeoutError):
        async with asyncio.timeout(0.05):
            await monitor.run()
    assert monitor.consecutive_failures == 0


async def test_monitor_raises_coma_after_threshold_consecutive_failures() -> None:
    monitor = HeartbeatMonitor(
        health=_scripted_health([False, False, False]),
        interval=0.001,
        threshold=3,
    )
    with pytest.raises(ComaDetected) as excinfo:
        await monitor.run()
    assert excinfo.value.consecutive_failures == 3
    assert monitor.consecutive_failures == 3


async def test_monitor_treats_health_exception_as_failure() -> None:
    monitor = HeartbeatMonitor(
        health=_scripted_health([RuntimeError("net"), RuntimeError("net")]),
        interval=0.001,
        threshold=2,
    )
    with pytest.raises(ComaDetected):
        await monitor.run()


async def test_monitor_successful_ping_resets_consecutive_failures() -> None:
    monitor = HeartbeatMonitor(
        health=_scripted_health(
            [False, False, True, False, False, True]
        ),
        interval=0.001,
        threshold=3,
    )
    # After 6 pings the script is: F F T F F T -> max run of failures = 2,
    # below threshold; the loop never raises.
    with pytest.raises(asyncio.TimeoutError):
        async with asyncio.timeout(0.05):
            await monitor.run()
    assert monitor.consecutive_failures <= 2


async def test_monitor_threshold_is_configurable() -> None:
    monitor = HeartbeatMonitor(
        health=_scripted_health([False, False]),
        interval=0.001,
        threshold=2,
    )
    with pytest.raises(ComaDetected) as excinfo:
        await monitor.run()
    assert excinfo.value.consecutive_failures == 2


async def test_monitor_honours_interval_between_polls() -> None:
    """Ten polls at 5ms each should take at least ~50ms.

    A naive implementation that forgets ``asyncio.sleep`` would burn
    through the script instantly. We use a generous bound (>=20ms for
    10 polls @ 5ms = nominal 50ms) to stay loose on slow CI machines.
    """
    health = _scripted_health([True] * 10)

    async def alive_for_a_bit() -> None:
        monitor = HeartbeatMonitor(
            health=health, interval=0.005, threshold=3
        )
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(0.06):
                await monitor.run()

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    await alive_for_a_bit()
    elapsed = loop.time() - t0
    assert elapsed >= 0.02


# --- HeartbeatConfig --------------------------------------------------------


def test_heartbeat_config_holds_health_interval_and_threshold() -> None:
    async def h() -> bool:
        return True

    cfg = HeartbeatConfig(health=h, interval_seconds=0.5, failure_threshold=5)
    assert cfg.health is h
    assert cfg.interval_seconds == 0.5
    assert cfg.failure_threshold == 5


def test_heartbeat_config_threshold_defaults_to_three() -> None:
    async def h() -> bool:
        return True

    cfg = HeartbeatConfig(health=h, interval_seconds=1.0)
    assert cfg.failure_threshold == 3
