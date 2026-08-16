"""Session ownership for delegation_get / delegation_stop, plus history bound."""

from __future__ import annotations

import asyncio
from pathlib import Path

from dream.subagents._async_delegation import AsyncDelegationManager, DelegationStatus
from dream.subagents._projection import SubagentResult
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.delegation_get import DelegationGetTool
from dream.tools.builtin.delegation_stop import DelegationStopTool


async def _quick() -> tuple[SubagentResult, ...]:
    return (SubagentResult(name="reviewer", output="ok"),)


def _ctx(session_id: str, manager: AsyncDelegationManager) -> ToolExecutionContext:
    return ToolExecutionContext(
        working_dir=Path("/tmp/test"),
        session_id=session_id,
        delegations=manager,
    )


async def test_get_and_stop_require_owning_session() -> None:
    manager = AsyncDelegationManager(max_active=1)
    release = asyncio.Event()

    async def work() -> tuple[SubagentResult, ...]:
        await release.wait()
        return (SubagentResult(name="reviewer", output="secret"),)

    handle = manager.start("owner", ("reviewer",), work)
    assert handle is not None
    assert manager.get(handle.delegation_id, session_id="intruder") is None
    assert await manager.stop(handle.delegation_id, session_id="intruder") is None
    assert manager.active("owner") == 1

    snap = manager.get(handle.delegation_id, session_id="owner")
    assert snap is not None
    assert snap.session_id == "owner"

    release.set()
    await manager.wait_next("owner")
    await manager.close()


async def test_delegation_tools_hide_foreign_session() -> None:
    manager = AsyncDelegationManager(max_active=1)
    handle = manager.start("owner", ("reviewer",), _quick)
    assert handle is not None
    await manager.wait_next("owner")

    get_tool = DelegationGetTool()
    stop_tool = DelegationStopTool()
    foreign = _ctx("intruder", manager)
    got = await get_tool.execute({"delegation_id": handle.delegation_id}, foreign)
    assert got.is_error
    assert "Unknown delegation_id" in got.content

    stopped = await stop_tool.execute({"delegation_id": handle.delegation_id}, foreign)
    assert stopped.is_error
    assert "Unknown delegation_id" in stopped.content

    owned = await get_tool.execute({"delegation_id": handle.delegation_id}, _ctx("owner", manager))
    assert not owned.is_error
    assert "secret" not in owned.content
    assert "ok" in owned.content
    await manager.close()


async def test_history_is_bounded() -> None:
    manager = AsyncDelegationManager(max_active=1, max_history=2)
    for index in range(3):
        handle = manager.start("owner", (f"agent-{index}",), _quick)
        assert handle is not None
        await manager.wait_next("owner")

    snaps = manager.list_for_session("owner")
    assert len(snaps) == 2
    names = {snap.subagent_names[0] for snap in snaps}
    assert names == {"agent-1", "agent-2"}
    await manager.close()


async def test_history_cap_is_per_session() -> None:
    manager = AsyncDelegationManager(max_active=1, max_history=2)
    other = manager.start("other", ("keep-me",), _quick)
    assert other is not None
    await manager.wait_next("other")

    for index in range(3):
        handle = manager.start("owner", (f"agent-{index}",), _quick)
        assert handle is not None
        await manager.wait_next("owner")

    owner = manager.list_for_session("owner")
    assert len(owner) == 2
    assert {snap.subagent_names[0] for snap in owner} == {"agent-1", "agent-2"}
    kept = manager.get(other.delegation_id, session_id="other")
    assert kept is not None
    assert kept.subagent_names == ("keep-me",)
    await manager.close()


async def test_stop_owned_active_delegation() -> None:
    manager = AsyncDelegationManager(max_active=1)

    async def hang() -> tuple[SubagentResult, ...]:
        await asyncio.Future()

    handle = manager.start("owner", ("reviewer",), hang)
    assert handle is not None
    await asyncio.sleep(0)
    snap = await manager.stop(handle.delegation_id, session_id="owner")
    assert snap is not None
    assert snap.status is DelegationStatus.STOPPED
    assert manager.active("owner") == 0
    await manager.close()
