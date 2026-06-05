"""Heartbeat + coma detection (Spec 03 stage 3b, acceptance #11/#12).

``HeartbeatMonitor.run()`` is a long-running coroutine that polls
``health()`` every ``interval`` seconds and raises ``ComaDetected``
after ``threshold`` consecutive failures (a ``False`` return or an
exception inside ``health``). A successful ping resets the counter.

The session orchestrator runs the monitor concurrently with each
turn's ``run_query`` and cancels the turn when ``ComaDetected``
fires. This module does NOT know about turns or sessions — it just
exposes the primitive and a config object the session can carry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


class ComaDetected(Exception):
    """Heartbeat failed ``threshold`` times in a row; cancel the turn."""

    def __init__(self, consecutive_failures: int) -> None:
        super().__init__(
            f"coma after {consecutive_failures} consecutive heartbeat failures"
        )
        self.consecutive_failures = consecutive_failures


class HeartbeatMonitor:
    def __init__(
        self,
        *,
        health: Callable[[], Awaitable[bool]],
        interval: float,
        threshold: int = 3,
    ) -> None:
        self._health = health
        self._interval = interval
        self._threshold = threshold
        self._consecutive_failures = 0

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            ok: bool
            try:
                ok = bool(await self._health())
            except Exception:
                ok = False
            if ok:
                self._consecutive_failures = 0
                continue
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._threshold:
                raise ComaDetected(self._consecutive_failures)


@dataclass
class HeartbeatConfig:
    health: Callable[[], Awaitable[bool]]
    interval_seconds: float
    failure_threshold: int = 3


__all__ = ["ComaDetected", "HeartbeatConfig", "HeartbeatMonitor"]
