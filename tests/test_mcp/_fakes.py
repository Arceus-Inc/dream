"""In-memory MCP test harness (underscore = pytest skips collection).

Builds *real* ``FastMCP`` servers and connects to them over the SDK's in-memory
transport, so the manager is tested against a genuine ``ClientSession`` — no
subprocess, no fake protocol.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session as _connect

from dream.mcp._client import SessionOpener, UnsupportedTransportError
from dream.mcp._types import AllowlistEntry


def build_server(
    name: str,
    *,
    tool_names: Iterable[str] = ("navigate",),
    resources: Iterable[tuple[str, str]] = (),
) -> FastMCP:
    """A real FastMCP server exposing ``tool_names`` (each takes an optional url)."""
    server = FastMCP(name)
    for tool_name in tool_names:
        server.tool(name=tool_name)(_make_tool(tool_name))
    for uri, body in resources:
        _register_resource(server, uri, body)
    return server


def opener_for(servers: dict[str, FastMCP]) -> SessionOpener:
    """A SessionOpener that connects to the in-memory server matching the entry."""

    @asynccontextmanager
    async def _opener(entry: AllowlistEntry) -> AsyncIterator[ClientSession]:
        server = servers.get(entry.name)
        if server is None:
            raise UnsupportedTransportError(f"no in-memory server for {entry.name!r}")
        async with _connect(server._mcp_server) as session:
            yield session

    return _opener


async def pin_for(server: FastMCP) -> str:
    """The pin string the manager should compute for ``server``'s real identity."""
    async with _connect(server._mcp_server) as session:
        init = await session.initialize()
        info = init.serverInfo
        digest = hashlib.sha256(f"{info.name}:{info.version}".encode()).hexdigest()
        return f"sha256:{digest}"


def _make_tool(tool_name: str):
    def _tool(url: str = "") -> str:
        return f"{tool_name}:{url}"

    _tool.__name__ = tool_name
    return _tool


def _register_resource(server: FastMCP, uri: str, body: str) -> None:
    @server.resource(uri)
    def _resource() -> str:
        return body
