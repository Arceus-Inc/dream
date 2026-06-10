"""``supervise_loop`` — crash isolation for the runtime's background loops.

Spec 15 P1 §3: every loop (cron tick, wake, watchers) gets crash-isolation
(log-as-event, continue) plus restart counters surfaced as ``runtime.health``
events, and a ceiling after which the loop is abandoned loudly (bounded
everything — spec 00 invariant 4).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dream.runtime._supervisor import supervise_loop


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, payload))
        return payload


async def _no_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_clean_return_ends_supervision() -> None:
    calls = 0

    async def loop() -> None:
        nonlocal calls
        calls += 1

    emit = _Recorder()
    await supervise_loop("cron", loop, emit=emit, sleep=_no_sleep)
    assert calls == 1
    assert emit.events == []


@pytest.mark.asyncio
async def test_crash_restarts_and_emits_health() -> None:
    calls = 0

    async def loop() -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError(f"boom {calls}")

    emit = _Recorder()
    await supervise_loop("wake", loop, emit=emit, sleep=_no_sleep)
    assert calls == 3
    health = [p for t, p in emit.events if t == "runtime.health"]
    assert [h["restarts"] for h in health] == [1, 2]
    assert all(h["loop"] == "wake" for h in health)
    assert "boom 1" in health[0]["error"]


@pytest.mark.asyncio
async def test_restart_ceiling_abandons_loudly() -> None:
    async def loop() -> None:
        raise RuntimeError("always")

    emit = _Recorder()
    await supervise_loop("cron", loop, emit=emit, max_restarts=2, sleep=_no_sleep)
    types = [t for t, _ in emit.events]
    assert types.count("runtime.health") == 3  # initial crash + 2 restarts
    assert types[-1] == "runtime.loop.abandoned"


@pytest.mark.asyncio
async def test_cancellation_propagates() -> None:
    started = asyncio.Event()

    async def loop() -> None:
        started.set()
        await asyncio.Event().wait()

    emit = _Recorder()
    task = asyncio.create_task(supervise_loop("cron", loop, emit=emit, sleep=_no_sleep))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Cancellation is shutdown, not a crash: no health events.
    assert emit.events == []


@pytest.mark.asyncio
async def test_backoff_grows_with_restarts() -> None:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    calls = 0

    async def loop() -> None:
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise RuntimeError("boom")

    emit = _Recorder()
    await supervise_loop(
        "cron", loop, emit=emit, backoff_seconds=2.0, sleep=fake_sleep
    )
    assert delays == [2.0, 4.0, 6.0]


@pytest.mark.asyncio
async def test_faulty_emitter_does_not_kill_supervision() -> None:
    calls = 0

    async def loop() -> None:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise RuntimeError("boom")

    def bad_emit(event_type: str, **payload: Any) -> None:
        raise ValueError("sink is broken")

    await supervise_loop("cron", loop, emit=bad_emit, sleep=_no_sleep)
    assert calls == 2
