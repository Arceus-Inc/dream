"""Spec 06 slice 4 — list_mcp_resources / read_mcp_resource ride the #05 contract.

Driven against a real ClientSession over the in-memory transport (the manager's
read path is genuine, not stubbed).
"""

from __future__ import annotations

from pathlib import Path

from dream.mcp._client import McpClientManager
from dream.mcp._types import AllowlistEntry
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.mcp_resources import ListMcpResourcesTool, ReadMcpResourceTool
from tests.test_mcp._fakes import build_server, opener_for


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s")


def _entry(name: str) -> AllowlistEntry:
    return AllowlistEntry(name=name, endpoint=f"stdio://{name}", transport="stdio")


async def _connected(servers: dict[str, object]) -> McpClientManager:
    entries = [_entry(name) for name in servers]
    mgr = McpClientManager(entries, session_opener=opener_for(servers))  # type: ignore[arg-type]
    await mgr.connect_all()
    return mgr


def test_list_tool_is_read_only() -> None:
    assert ListMcpResourcesTool(_DummyManager()).is_read_only() is True


def test_read_tool_is_read_only() -> None:
    assert ReadMcpResourceTool(_DummyManager()).is_read_only() is True


async def test_list_resources_renders_lines(tmp_path: Path) -> None:
    server = build_server("pw", resources=(("file:///readme", "body"),))
    mgr = await _connected({"pw": server})
    result = await ListMcpResourcesTool(mgr).execute({}, _ctx(tmp_path))
    assert result.is_error is False
    assert "pw" in result.content
    assert "file:///readme" in result.content
    await mgr.close()


async def test_list_resources_empty(tmp_path: Path) -> None:
    mgr = await _connected({"pw": build_server("pw")})
    result = await ListMcpResourcesTool(mgr).execute({}, _ctx(tmp_path))
    assert result.is_error is False
    assert "no MCP resources" in result.content
    await mgr.close()


async def test_read_resource_returns_body(tmp_path: Path) -> None:
    server = build_server("pw", resources=(("file:///readme", "hello body"),))
    mgr = await _connected({"pw": server})
    result = await ReadMcpResourceTool(mgr).execute(
        {"server": "pw", "uri": "file:///readme"}, _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "hello body" in result.content
    await mgr.close()


async def test_read_resource_disconnected_is_tool_error(tmp_path: Path) -> None:
    mgr = McpClientManager([_entry("pw")], session_opener=opener_for({}))
    await mgr.connect_all()  # pw failed to connect
    result = await ReadMcpResourceTool(mgr).execute(
        {"server": "pw", "uri": "file:///x"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata


class _DummyManager:
    """Identity-only stand-in for read-only assertions."""
