"""Behavior tests for background delegation ownership and delivery."""

from __future__ import annotations

import asyncio

from dream.subagents._async_delegation import AsyncDelegationManager, DelegationStatus
from dream.subagents._projection import SubagentResult


async def test_completion_is_owned_by_session_and_preserves_result_order() -> None:
    manager = AsyncDelegationManager(max_active=2)
    release = asyncio.Event()

    async def work() -> tuple[SubagentResult, ...]:
        await release.wait()
        return (
            SubagentResult(name="slow", output="first"),
            SubagentResult(name="fast", output="second"),
        )

    handle = manager.start("parent", ("slow", "fast"), work)
    assert handle is not None
    assert manager.drain("other") == ()

    release.set()
    completion = await manager.wait_next("parent")

    assert completion.delegation_id == handle.delegation_id
    assert completion.status is DelegationStatus.COMPLETED
    assert [result.name for result in completion.results] == ["slow", "fast"]
    assert manager.active("parent") == 0
    await manager.close()


async def test_capacity_refusal_does_not_start_work() -> None:
    manager = AsyncDelegationManager(max_active=1)
    release = asyncio.Event()
    calls = 0

    async def work() -> tuple[SubagentResult, ...]:
        nonlocal calls
        calls += 1
        await release.wait()
        return (SubagentResult(name="reviewer", output="ok"),)

    assert manager.start("one", ("reviewer",), work) is not None
    assert manager.start("two", ("reviewer",), work) is None
    await asyncio.sleep(0)
    assert calls == 1

    release.set()
    await manager.wait_next("one")
    await manager.close()


async def test_cancel_session_interrupts_children_without_orphans() -> None:
    manager = AsyncDelegationManager(max_active=1)
    cancelled = asyncio.Event()

    async def work() -> tuple[SubagentResult, ...]:
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    assert manager.start("parent", ("reviewer",), work) is not None
    await asyncio.sleep(0)
    await manager.cancel_session("parent")

    assert cancelled.is_set()
    assert manager.active("parent") == 0
    await manager.close()


async def test_cancelled_background_work_delivers_typed_completion() -> None:
    manager = AsyncDelegationManager(max_active=1)

    async def work() -> tuple[SubagentResult, ...]:
        await asyncio.Future()

    assert manager.start("parent", ("reviewer",), work) is not None
    await asyncio.sleep(0)
    waiter = asyncio.create_task(manager.wait_next("parent"))
    await asyncio.sleep(0)
    await manager.cancel_session("parent")
    completion = await waiter

    assert completion.status is DelegationStatus.STOPPED
    assert completion.error == "background delegation stopped"
    await manager.close()


async def test_timeout_becomes_a_typed_completion() -> None:
    manager = AsyncDelegationManager(max_active=1, timeout_seconds=0.01)

    async def work() -> tuple[SubagentResult, ...]:
        await asyncio.Future()

    assert manager.start("parent", ("reviewer",), work) is not None
    completion = await manager.wait_next("parent")

    assert completion.status is DelegationStatus.TIMED_OUT
    assert "exceeded" in (completion.error or "")
    assert manager.active("parent") == 0
    await manager.close()
