"""Default transport openers for the MCP client (Spec 06 slice 4).

The manager drives a real ``ClientSession``; *how* a session is established for an
allowlist entry is the one seam (``SessionOpener``). This module is the production
opener covering all three transports — stdio, streamable HTTP, and WebSocket —
with gitignored credentials merged in at connect time, so a freshly written
secret takes effect on the next (re)connect.

WebSocket caveat: the SDK's ``websocket_client(url)`` takes no headers, so
header-mode credentials cannot be injected over ws — ws connects by URL only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from dream.mcp._allowlist import entry_to_config
from dream.mcp._credentials import ServerCredential, apply_credentials, read_credentials
from dream.mcp._types import AllowlistEntry, McpHttpServerConfig, McpStdioServerConfig

try:  # the ws transport needs the optional ``websockets`` runtime
    from mcp.client.websocket import websocket_client
except ImportError:  # pragma: no cover - websockets is a declared dependency
    websocket_client = None  # type: ignore[assignment]


SessionOpener = Callable[[AllowlistEntry], AbstractAsyncContextManager[ClientSession]]


class UnsupportedTransportError(RuntimeError):
    """Raised by an opener for a transport this build can't connect."""


def make_default_opener(credentials_path: Path | None) -> SessionOpener:
    """Build the production opener; reads credentials fresh on each connect."""

    @asynccontextmanager
    async def _opener(entry: AllowlistEntry) -> AsyncIterator[ClientSession]:
        config = apply_credentials(
            entry_to_config(entry), _credential_for(entry, credentials_path)
        )
        if isinstance(config, McpStdioServerConfig):
            params = StdioServerParameters(
                command=config.command, args=config.args, env=config.env, cwd=config.cwd
            )
            async with (
                stdio_client(params) as (read, write),
                ClientSession(read, write) as session,
            ):
                yield session
        elif isinstance(config, McpHttpServerConfig):
            async with (
                streamablehttp_client(config.url, headers=config.headers or None) as (
                    read,
                    write,
                    _get_session_id,
                ),
                ClientSession(read, write) as session,
            ):
                yield session
        else:  # McpWebSocketServerConfig
            if websocket_client is None:
                raise UnsupportedTransportError(
                    "ws transport requires the 'websockets' package, which is not installed"
                )
            async with (
                websocket_client(config.url) as (read, write),
                ClientSession(read, write) as session,
            ):
                yield session

    return _opener


def _credential_for(entry: AllowlistEntry, credentials_path: Path | None) -> ServerCredential | None:
    if credentials_path is None:
        return None
    return read_credentials(credentials_path).get(entry.name)


__all__ = ["SessionOpener", "UnsupportedTransportError", "make_default_opener"]
