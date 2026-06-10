"""Crash isolation for the runtime's background loops (spec 15 P1 §3).

A supervised loop never takes the process down and never dies silently:
each crash is emitted as a ``runtime.health`` event with a restart
counter, restarts back off linearly, and a bounded ceiling abandons the
loop with a final ``runtime.loop.abandoned`` event (spec 00 invariant 4:
bounded everything, escalate at the cap).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

__all__ = ["EmitFn", "supervise_loop"]


class EmitFn(Protocol):
    """Event emitter shape — :meth:`dream.observability.EventSink.emit`."""

    def __call__(self, event_type: str, **payload: Any) -> Any: ...


def _emit_safe(emit: EmitFn, event_type: str, **payload: Any) -> None:
    # The sink is untrusted from the supervisor's perspective: a faulty
    # emitter must not become a second crash loop on top of the first.
    with contextlib.suppress(Exception):
        emit(event_type, **payload)


async def supervise_loop(
    name: str,
    factory: Callable[[], Awaitable[None]],
    *,
    emit: EmitFn,
    max_restarts: int = 5,
    backoff_seconds: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Run ``factory()`` until it returns cleanly, restarting on crashes.

    ``asyncio.CancelledError`` propagates — cancellation is shutdown, not
    a crash. Every crash emits ``runtime.health``; crash ``max_restarts+1``
    abandons the loop with ``runtime.loop.abandoned`` instead of retrying
    forever.
    """
    crashes = 0
    while True:
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            crashes += 1
            _emit_safe(
                emit,
                "runtime.health",
                loop=name,
                error=repr(exc),
                restarts=crashes,
            )
            if crashes > max_restarts:
                _emit_safe(
                    emit,
                    "runtime.loop.abandoned",
                    loop=name,
                    restarts=crashes,
                )
                return
            await sleep(backoff_seconds * crashes)
        else:
            return
