"""Spec 06 slice 4 — the /mcp REPL command surfaces per-server status."""

from __future__ import annotations

import io

from dream.mcp._client import McpClientManager
from dream.mcp._types import AllowlistEntry
from dream.repl._session import _cmd_mcp
from tests.test_mcp._fakes import build_server, opener_for


def _entry(name: str) -> AllowlistEntry:
    return AllowlistEntry(name=name, endpoint=f"stdio://{name}", transport="stdio")


async def _connected(servers: dict[str, object]) -> McpClientManager:
    mgr = McpClientManager(
        [_entry(n) for n in servers], session_opener=opener_for(servers)  # type: ignore[arg-type]
    )
    await mgr.connect_all()
    return mgr


def test_cmd_mcp_no_manager_says_none() -> None:
    out = io.StringIO()
    _cmd_mcp(None, output=out, use=False)
    assert "no MCP" in out.getvalue()


async def test_cmd_mcp_lists_connected_servers() -> None:
    mgr = await _connected({"pw": build_server("pw", tool_names=("navigate",))})
    out = io.StringIO()
    _cmd_mcp(mgr, output=out, use=False)
    text = out.getvalue()
    assert "pw" in text
    assert "connected" in text
    await mgr.close()
