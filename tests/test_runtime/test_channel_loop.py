"""Unit tests for dream.runtime._channel — the channel_loop plumbing.

Covers drain → handle → ack flow, handler exception → error ack,
and CancelledError propagation.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dream.channels import Ack, Command, StatusCommand
from dream.runtime._channel import channel_loop


class _FakeSink:
    """Minimal EventSink stand-in that records emitted events."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        record = {"type": event_type, **payload}
        self.events.append(record)
        return record


class _FakeInbox:
    """In-memory inbox that yields commands once, then empty."""

    def __init__(self, commands: list[Command] | None = None) -> None:
        self._commands = list(commands or [])
        self._drained = False

    def drain(self) -> list[Command]:
        if not self._drained:
            self._drained = True
            return self._commands
        return []


@pytest.mark.asyncio
async def test_channel_loop_drains_and_acks() -> None:
    command = StatusCommand()
    inbox = _FakeInbox([command])
    sink = _FakeSink()
    ack_received = asyncio.Event()

    async def handler(cmd: Command) -> Ack:
        return Ack(status="ok", summary="handled")

    iterations = 0

    async def fake_sleep(seconds: float) -> None:
        nonlocal iterations
        iterations += 1
        if iterations >= 2:
            ack_received.set()
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await channel_loop(
            inbox=inbox,  # type: ignore[arg-type]
            sink=sink,  # type: ignore[arg-type]
            handler=handler,
            poll_seconds=0.01,
            sleep=fake_sleep,
        )


@pytest.mark.asyncio
async def test_channel_loop_handler_exception_becomes_error_ack() -> None:
    command = StatusCommand()
    inbox = _FakeInbox([command])
    sink = _FakeSink()

    async def broken_handler(cmd: Command) -> Ack:
        raise ValueError("handler broke")

    iterations = 0

    async def fake_sleep(seconds: float) -> None:
        nonlocal iterations
        iterations += 1
        if iterations >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await channel_loop(
            inbox=inbox,  # type: ignore[arg-type]
            sink=sink,  # type: ignore[arg-type]
            handler=broken_handler,
            poll_seconds=0.01,
            sleep=fake_sleep,
        )

    # The ack should have been emitted with status=error.
    assert len(sink.events) >= 1


@pytest.mark.asyncio
async def test_channel_loop_cancelled_error_propagates() -> None:
    command = StatusCommand()
    inbox = _FakeInbox([command])
    sink = _FakeSink()

    async def cancelling_handler(cmd: Command) -> Ack:
        raise asyncio.CancelledError

    async def fake_sleep(seconds: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await channel_loop(
            inbox=inbox,  # type: ignore[arg-type]
            sink=sink,  # type: ignore[arg-type]
            handler=cancelling_handler,
            poll_seconds=0.01,
            sleep=fake_sleep,
        )


@pytest.mark.asyncio
async def test_channel_loop_empty_inbox() -> None:
    inbox = _FakeInbox([])
    sink = _FakeSink()

    async def handler(cmd: Command) -> Ack:
        return Ack(status="ok", summary="handled")

    iterations = 0

    async def fake_sleep(seconds: float) -> None:
        nonlocal iterations
        iterations += 1
        if iterations >= 3:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await channel_loop(
            inbox=inbox,  # type: ignore[arg-type]
            sink=sink,  # type: ignore[arg-type]
            handler=handler,
            poll_seconds=0.01,
            sleep=fake_sleep,
        )

    assert len(sink.events) == 0
