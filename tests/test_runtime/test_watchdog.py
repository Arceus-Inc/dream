"""Liveness watchdog (spec 15 P3 §2 / spec 10p5).

The runtime walks the coordination board on a loop: an expired lease on
a claimed/executing row means the owning runner died (or wedged). The
watchdog emits ``runtime.watchdog.stale_claim`` once per lease epoch and
hands the claim to an optional policy handler — requeue/takeover/abandon
is the consumer's decision, the detection is the SDK's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dream.coordination import BoardStore, Claim
from dream.runtime._watchdog import find_stale_claims, watchdog_loop


def _claim(
    task_id: str,
    *,
    state: str = "executing",
    lease_expires_at_ms: int | None = 1_000,
) -> Claim:
    return Claim(
        task_id=task_id,
        state=state,  # type: ignore[arg-type]
        checkout_run_id="co-1",
        execution_run_id="ex-1",
        claimed_by="runner-a",
        claimed_at_ms=500,
        lease_expires_at_ms=lease_expires_at_ms,
        last_heartbeat_at_ms=900,
    )


def _board_with(tmp_path: Path, *claims: Claim) -> Path:
    board_path = tmp_path / "board.sqlite"
    with BoardStore(board_path) as board:
        for claim in claims:
            with board.transaction() as tx:
                tx.upsert(claim)
    return board_path


def test_find_stale_claims_detects_expired_lease(tmp_path: Path) -> None:
    board_path = _board_with(
        tmp_path,
        _claim("t-dead", lease_expires_at_ms=1_000),
        _claim("t-live", lease_expires_at_ms=99_999),
    )
    with BoardStore(board_path) as board:
        stale = find_stale_claims(board, now_ms=5_000)
    assert [c.task_id for c in stale] == ["t-dead"]


def test_releasing_and_unleased_rows_are_not_stale(tmp_path: Path) -> None:
    board_path = _board_with(
        tmp_path,
        _claim("t-releasing", state="releasing", lease_expires_at_ms=1_000),
        _claim("t-no-lease", lease_expires_at_ms=None),
    )
    with BoardStore(board_path) as board:
        assert find_stale_claims(board, now_ms=5_000) == ()


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, payload))
        return payload


class _StopLoop(Exception):
    pass


def _sleeper(max_ticks: int) -> Any:
    ticks = 0

    async def sleep(seconds: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks > max_ticks:
            raise _StopLoop

    return sleep


@pytest.mark.asyncio
async def test_loop_emits_once_per_lease_epoch(tmp_path: Path) -> None:
    board_path = _board_with(tmp_path, _claim("t-dead", lease_expires_at_ms=1_000))
    emit = _Recorder()
    handled: list[Claim] = []

    async def on_stale(claim: Claim) -> None:
        handled.append(claim)

    with pytest.raises(_StopLoop):
        await watchdog_loop(
            board_path=board_path,
            emit=emit,
            on_stale=on_stale,
            poll_seconds=0,
            sleep=_sleeper(max_ticks=3),
            now_ms=lambda: 5_000,
        )
    stale_events = [p for t, p in emit.events if t == "runtime.watchdog.stale_claim"]
    # Three ticks over the same expired lease → exactly one emission.
    assert len(stale_events) == 1
    assert stale_events[0]["task_id"] == "t-dead"
    assert stale_events[0]["claimed_by"] == "runner-a"
    assert [c.task_id for c in handled] == ["t-dead"]


@pytest.mark.asyncio
async def test_loop_tolerates_missing_board(tmp_path: Path) -> None:
    emit = _Recorder()
    with pytest.raises(_StopLoop):
        await watchdog_loop(
            board_path=tmp_path / "missing.sqlite",
            emit=emit,
            on_stale=None,
            poll_seconds=0,
            sleep=_sleeper(max_ticks=2),
            now_ms=lambda: 5_000,
        )
    assert emit.events == []


@pytest.mark.asyncio
async def test_new_lease_epoch_re_emits(tmp_path: Path) -> None:
    board_path = _board_with(tmp_path, _claim("t-dead", lease_expires_at_ms=1_000))
    emit = _Recorder()
    ticks = 0

    async def sleep(seconds: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            # Another runner reclaimed and died again: a fresh lease, expired.
            with BoardStore(board_path) as board, board.transaction() as tx:
                tx.upsert(_claim("t-dead", lease_expires_at_ms=2_000))
        if ticks > 3:
            raise _StopLoop

    with pytest.raises(_StopLoop):
        await watchdog_loop(
            board_path=board_path,
            emit=emit,
            on_stale=None,
            poll_seconds=0,
            sleep=sleep,
            now_ms=lambda: 5_000,
        )
    stale_events = [p for t, p in emit.events if t == "runtime.watchdog.stale_claim"]
    assert len(stale_events) == 2
