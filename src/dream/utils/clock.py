"""Injectable clock seam for lease/heartbeat math (Spec 08).

Coordination computes lease expiry, grace, and stall windows against "now". A
single ``Clock`` seam lets tests advance time deterministically (and lets later
specs — #07 cron, #10p5 liveness — reuse the same injection point instead of
hardcoding ``time.time()``). Time is epoch milliseconds throughout, matching the
coordination store's integer columns.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """A source of the current time in epoch milliseconds."""

    def now_ms(self) -> int:
        """Milliseconds since the Unix epoch."""
        ...


class SystemClock:
    """Wall-clock backed by :func:`time.time`."""

    def now_ms(self) -> int:
        return int(time.time() * 1000)


class FakeClock:
    """Deterministic clock for tests: fixed start, advanced explicitly."""

    def __init__(self, start_ms: int = 0) -> None:
        self._now_ms = start_ms

    def now_ms(self) -> int:
        return self._now_ms

    def advance(self, ms: int) -> None:
        """Move time forward by ``ms`` milliseconds (never backwards)."""
        if ms < 0:
            raise ValueError(f"cannot advance time backwards: {ms}")
        self._now_ms += ms


__all__ = ["Clock", "FakeClock", "SystemClock"]
