"""Channel poll loop — drain the inbox, handle, ack (spec 15 P2 §1).

The loop itself is deterministic plumbing: drain → handle → ack, then
sleep. A handler exception becomes an ``error`` ack rather than a loop
crash — the supervisor above this would restart the loop, but the
command that broke it would be gone; answering with an error keeps the
sender informed and the channel alive.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from dream.channels import Ack, Command, CommandInbox
from dream.observability import EventSink

__all__ = ["channel_loop"]

CommandHandler = Callable[[Command], Awaitable[Ack]]


async def channel_loop(
    *,
    inbox: CommandInbox,
    sink: EventSink,
    handler: CommandHandler,
    poll_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    while True:
        for command in inbox.drain():
            try:
                ack = await handler(command)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                ack = Ack(status="error", summary=f"command handler raised: {exc!r}")
            ack.emit(sink, command_id=command.id)
        await sleep(poll_seconds)
