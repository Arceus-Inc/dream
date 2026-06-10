"""Liveness watchdog — expired leases on the coordination board (spec 10p5 / 15 P3 §2).

A claimed/executing row whose lease has expired means the owning runner
died or wedged: nothing is heartbeating it, yet the task looks taken.
The watchdog detects (acts on *leases*, never heuristics — spec 15 §4),
emits ``runtime.watchdog.stale_claim`` once per lease epoch, and hands
the claim to an optional async policy handler. Requeue / takeover /
abandon is the consumer's call: a takeover needs the consumer's own
``ClaimManager.reclaim`` with its runner identity.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from dream.coordination import BoardStore, Claim
from dream.runtime._supervisor import EmitFn

__all__ = ["find_stale_claims", "watchdog_loop"]


def _now_ms() -> int:
    return int(time.time() * 1000)


def find_stale_claims(board: BoardStore, *, now_ms: int) -> tuple[Claim, ...]:
    """Rows in claimed/executing whose lease expired before ``now_ms``.

    ``releasing`` rows are mid-handback (not lost) and rows without a
    lease were never live-owned — neither is stale.
    """
    return tuple(
        claim
        for claim in board.list_claims()
        if claim.state in ("claimed", "executing")
        and claim.lease_expires_at_ms is not None
        and claim.lease_expires_at_ms < now_ms
    )


async def watchdog_loop(
    *,
    board_path: Path,
    emit: EmitFn,
    on_stale: Callable[[Claim], Awaitable[None]] | None = None,
    poll_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now_ms: Callable[[], int] = _now_ms,
) -> None:
    """Walk the board every ``poll_seconds``; report each stale lease once.

    The dedup key is ``(task_id, lease_expires_at_ms)`` — a *new* expired
    lease on the same task (reclaimed, then died again) is a fresh
    incident and is re-emitted. A missing board file just means no swarm
    has run yet; the loop keeps ticking until one appears.
    """
    reported: dict[str, int] = {}
    while True:
        await sleep(poll_seconds)
        if not board_path.exists():
            continue
        with BoardStore(board_path) as board:
            stale = find_stale_claims(board, now_ms=now_ms())
        for claim in stale:
            lease = claim.lease_expires_at_ms
            assert lease is not None  # find_stale_claims guarantees it
            if reported.get(claim.task_id) == lease:
                continue
            reported[claim.task_id] = lease
            emit(
                "runtime.watchdog.stale_claim",
                task_id=claim.task_id,
                state=claim.state,
                claimed_by=claim.claimed_by,
                lease_expires_at_ms=lease,
                expired_for_ms=now_ms() - lease,
            )
            if on_stale is not None:
                await on_stale(claim)