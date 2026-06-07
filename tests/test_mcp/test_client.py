"""Spec 06 — McpClientManager: connect, pin, deny-undeclared, status, calls.

Driven against a real ``ClientSession`` over the SDK's in-memory transport
(``mcp.shared.memory``) — no subprocess, no fake protocol.
"""

from __future__ import annotations

import pytest

from dream.mcp._client import McpClientManager, McpServerNotConnectedError
from dream.mcp._types import AllowlistEntry
from dream.services.repo_validator import has_blocking
from tests.test_mcp._fakes import build_server, opener_for, pin_for


def _entry(name: str, **kw: object) -> AllowlistEntry:
    return AllowlistEntry(
        name=name,
        endpoint=f"stdio://{name}",
        transport="stdio",
        **kw,  # type: ignore[arg-type]
    )


async def test_connect_marks_connected_and_lists_tools() -> None:
    server = build_server("pw", tool_names=("navigate",))
    entry = _entry("pw")
    mgr = McpClientManager([entry], session_opener=opener_for({"pw": server}))
    findings = await mgr.connect_all()
    assert findings == []
    status = mgr.status("pw")
    assert status is not None and status.state == "connected"
    assert [t.name for t in mgr.list_tools()] == ["navigate"]
    assert mgr.list_tools()[0].server_name == "pw"
    await mgr.close()


async def test_pin_mismatch_refuses() -> None:
    server = build_server("pw")
    entry = _entry("pw", pinned_version_hash="sha256:deadbeef")
    mgr = McpClientManager([entry], session_opener=opener_for({"pw": server}))
    findings = await mgr.connect_all()
    assert has_blocking(findings)
    assert mgr.status("pw").state == "failed"  # type: ignore[union-attr]
    assert mgr.list_tools() == []
    await mgr.close()


async def test_pin_match_connects() -> None:
    server = build_server("pw")
    entry = _entry("pw", pinned_version_hash=await pin_for(server))
    mgr = McpClientManager([entry], session_opener=opener_for({"pw": server}))
    findings = await mgr.connect_all()
    assert findings == []
    assert mgr.status("pw").state == "connected"  # type: ignore[union-attr]
    await mgr.close()


async def test_deny_undeclared_tools() -> None:
    server = build_server("pw", tool_names=("navigate", "click", "exec_shell"))
    entry = _entry("pw", tools=("navigate", "click"))
    mgr = McpClientManager([entry], session_opener=opener_for({"pw": server}))
    await mgr.connect_all()
    assert sorted(t.name for t in mgr.list_tools()) == ["click", "navigate"]
    await mgr.close()


async def test_empty_tools_list_admits_all() -> None:
    server = build_server("pw", tool_names=("a", "b"))
    entry = _entry("pw")  # tools=() -> operator did not narrow
    mgr = McpClientManager([entry], session_opener=opener_for({"pw": server}))
    await mgr.connect_all()
    assert sorted(t.name for t in mgr.list_tools()) == ["a", "b"]
    await mgr.close()


async def test_unsupported_transport_marks_failed_not_crash() -> None:
    entry = _entry("ws_server")
    mgr = McpClientManager([entry], session_opener=opener_for({}))  # no server -> raises
    findings = await mgr.connect_all()
    assert findings == []  # non-fatal
    assert mgr.status("ws_server").state == "failed"  # type: ignore[union-attr]


async def test_call_tool_routes_to_session() -> None:
    server = build_server("pw", tool_names=("navigate",))
    mgr = McpClientManager([_entry("pw")], session_opener=opener_for({"pw": server}))
    await mgr.connect_all()
    out = await mgr.call_tool("pw", "navigate", {"url": "x"})
    assert out == "navigate:x"
    await mgr.close()


async def test_call_tool_disconnected_raises() -> None:
    mgr = McpClientManager([_entry("pw")], session_opener=opener_for({}))
    await mgr.connect_all()  # pw failed to connect
    with pytest.raises(McpServerNotConnectedError):
        await mgr.call_tool("pw", "navigate", {})


async def test_resources_surfaced_in_status() -> None:
    server = build_server("pw", resources=(("file:///r", "body"),))
    mgr = McpClientManager([_entry("pw")], session_opener=opener_for({"pw": server}))
    await mgr.connect_all()
    assert [r.uri for r in mgr.list_resources()] == ["file:///r"]
    await mgr.close()


async def test_close_clears_sessions() -> None:
    server = build_server("pw")
    mgr = McpClientManager([_entry("pw")], session_opener=opener_for({"pw": server}))
    await mgr.connect_all()
    await mgr.close()
    with pytest.raises(McpServerNotConnectedError):
        await mgr.call_tool("pw", "navigate", {})


async def test_list_statuses_is_name_sorted() -> None:
    servers = {"zeta": build_server("zeta"), "alpha": build_server("alpha")}
    entries = [_entry("zeta"), _entry("alpha")]
    mgr = McpClientManager(entries, session_opener=opener_for(servers))
    await mgr.connect_all()
    assert [s.name for s in mgr.list_statuses()] == ["alpha", "zeta"]
    await mgr.close()
