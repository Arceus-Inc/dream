"""MCP client manager — connect admitted servers, expose tools/resources.

Uses the official ``mcp`` SDK directly: the manager drives a real
``ClientSession`` per server. The only seam is the *opener* — how a session is
established for an entry — so tests substitute the SDK's in-memory transport
(``mcp.shared.memory``) and exercise the real ``ClientSession``, while the
default opener spawns a stdio server.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import AnyUrl

from dream.mcp._allowlist import entry_to_config
from dream.mcp._types import (
    AllowlistEntry,
    McpConnectionStatus,
    McpResourceInfo,
    McpStdioServerConfig,
    McpToolInfo,
)
from dream.services.repo_validator import Finding


class McpServerNotConnectedError(RuntimeError):
    """Raised when a tool/resource call targets a server with no live session."""


class UnsupportedTransportError(RuntimeError):
    """Raised by an opener for a transport this build can't connect."""


SessionOpener = Callable[[AllowlistEntry], AbstractAsyncContextManager[ClientSession]]


@asynccontextmanager
async def _default_opener(entry: AllowlistEntry) -> AsyncIterator[ClientSession]:
    """Open a real stdio ``ClientSession`` for ``entry`` (http/ws land in slice 4)."""
    config = entry_to_config(entry)
    if not isinstance(config, McpStdioServerConfig):
        raise UnsupportedTransportError(
            f"transport {entry.transport!r} is not supported in this build"
        )
    params = StdioServerParameters(
        command=config.command, args=config.args, env=config.env, cwd=config.cwd
    )
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        yield session


class McpClientManager:
    """Manage MCP connections for the admitted allowlist entries."""

    def __init__(
        self,
        entries: list[AllowlistEntry],
        *,
        session_opener: SessionOpener | None = None,
    ) -> None:
        self._entries: dict[str, AllowlistEntry] = {e.name: e for e in entries}
        self._opener: SessionOpener = session_opener or _default_opener
        self._statuses: dict[str, McpConnectionStatus] = {
            e.name: McpConnectionStatus(name=e.name, state="pending", transport=e.transport)
            for e in entries
        }
        self._sessions: dict[str, ClientSession] = {}
        self._stacks: dict[str, AsyncExitStack] = {}

    async def connect_all(self) -> list[Finding]:
        """Connect every entry; return blocking findings (e.g. a pin mismatch)."""
        findings: list[Finding] = []
        for name, entry in self._entries.items():
            findings.extend(await self._connect_one(name, entry))
        return findings

    async def close(self) -> None:
        """Close all live sessions."""
        for stack in list(self._stacks.values()):
            await _safe_aclose(stack)
        self._stacks.clear()
        self._sessions.clear()

    async def reconnect_all(self) -> list[Finding]:
        """Close then reconnect every entry."""
        await self.close()
        for name, entry in self._entries.items():
            self._statuses[name] = McpConnectionStatus(
                name=name, state="pending", transport=entry.transport
            )
        return await self.connect_all()

    def list_statuses(self) -> list[McpConnectionStatus]:
        """Per-server status, name-sorted."""
        return [self._statuses[name] for name in sorted(self._statuses)]

    def status(self, name: str) -> McpConnectionStatus | None:
        """One server's status, or ``None``."""
        return self._statuses.get(name)

    def entry_for(self, name: str) -> AllowlistEntry | None:
        """The allowlist entry for ``name`` (for tier lookup at registration)."""
        return self._entries.get(name)

    def list_tools(self) -> list[McpToolInfo]:
        """All admitted tools across connected servers."""
        tools: list[McpToolInfo] = []
        for status in self.list_statuses():
            tools.extend(status.tools)
        return tools

    def list_resources(self) -> list[McpResourceInfo]:
        """All resources across connected servers."""
        resources: list[McpResourceInfo] = []
        for status in self.list_statuses():
            resources.extend(status.resources)
        return resources

    async def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        """Invoke a tool; raises :class:`McpServerNotConnectedError` if down."""
        session = self._require_session(server)
        try:
            result = await session.call_tool(tool, arguments)
        except Exception as exc:
            raise McpServerNotConnectedError(f"MCP server {server!r} call failed: {exc}") from exc
        return _stringify_content(result.content)

    async def read_resource(self, server: str, uri: str) -> str:
        """Read a resource; raises :class:`McpServerNotConnectedError` if down."""
        session = self._require_session(server)
        try:
            result = await session.read_resource(AnyUrl(uri))
        except Exception as exc:
            raise McpServerNotConnectedError(
                f"MCP server {server!r} resource read failed: {exc}"
            ) from exc
        parts = [getattr(item, "text", None) or "" for item in result.contents]
        return "\n".join(p for p in parts if p).strip()

    # --- internals -----------------------------------------------------------

    def _require_session(self, server: str) -> ClientSession:
        session = self._sessions.get(server)
        if session is None:
            status = self._statuses.get(server)
            detail = status.detail if status else "unknown server"
            raise McpServerNotConnectedError(
                f"MCP server {server!r} is not connected: {detail}"
            )
        return session

    async def _connect_one(self, name: str, entry: AllowlistEntry) -> list[Finding]:
        stack = AsyncExitStack()
        try:
            session = await stack.enter_async_context(self._opener(entry))
            init = await session.initialize()
        except Exception as exc:
            await _safe_aclose(stack)
            self._mark_failed(name, entry, exc)
            return []

        info = init.serverInfo
        pin_finding = self._verify_pin(name, entry, info.name, info.version)
        if pin_finding is not None:
            await _safe_aclose(stack)
            return [pin_finding]

        try:
            tools = self._admit_tools(name, entry, await session.list_tools())
            resources = await self._safe_resources(name, session)
        except Exception as exc:
            await _safe_aclose(stack)
            self._mark_failed(name, entry, exc)
            return []

        self._sessions[name] = session
        self._stacks[name] = stack
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state="connected",
            transport=entry.transport,
            tools=tools,
            resources=resources,
        )
        return []

    def _verify_pin(
        self, name: str, entry: AllowlistEntry, server_name: str, server_version: str
    ) -> Finding | None:
        if not entry.pinned_version_hash:
            return None  # unpinned: operator opted out of the supply-chain guard (#11)
        expected = entry.pinned_version_hash.split(":", 1)[-1].strip().lower()
        actual = hashlib.sha256(f"{server_name}:{server_version}".encode()).hexdigest()
        if actual == expected:
            return None
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state="failed",
            transport=entry.transport,
            detail=f"version pin mismatch (pinned {expected[:12]}…, got {actual[:12]}…)",
        )
        return Finding(
            severity="blocking",
            code="mcp_version_mismatch",
            message=f"MCP version mismatch for {name!r}: pinned hash does not match the server",
        )

    def _admit_tools(
        self, name: str, entry: AllowlistEntry, result: Any
    ) -> list[McpToolInfo]:
        declared = set(entry.tools)
        # Empty ``tools`` means the operator did not narrow → admit all advertised;
        # otherwise undeclared tools are denied (#14).
        return [
            McpToolInfo(
                server_name=name,
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.inputSchema or {}),
            )
            for tool in result.tools
            if not declared or tool.name in declared
        ]

    async def _safe_resources(self, name: str, session: ClientSession) -> list[McpResourceInfo]:
        try:
            result = await session.list_resources()
        except Exception:
            return []  # server doesn't implement resources — non-fatal
        return [
            McpResourceInfo(
                server_name=name,
                name=resource.name or str(resource.uri),
                uri=str(resource.uri),
                description=resource.description or "",
            )
            for resource in result.resources
        ]

    def _mark_failed(self, name: str, entry: AllowlistEntry, exc: BaseException) -> None:
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state="failed",
            transport=entry.transport,
            detail=str(exc) or type(exc).__name__,
        )


async def _safe_aclose(stack: AsyncExitStack) -> None:
    with contextlib.suppress(Exception):
        await stack.aclose()


def _stringify_content(content: list[Any]) -> str:
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
        elif hasattr(item, "model_dump_json"):
            parts.append(item.model_dump_json())
        else:
            parts.append(str(item))
    return "\n".join(parts).strip() or "(no output)"


__all__ = [
    "McpClientManager",
    "McpServerNotConnectedError",
    "SessionOpener",
    "UnsupportedTransportError",
]
