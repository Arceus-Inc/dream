"""Model Context Protocol — allowlist admission, client, and tool adapters (Spec 06).

MCP servers are external tool/resource providers admitted only through a per-repo
allowlist (the authority), connected over a typed transport, and adapted onto the
``#05`` tool contract. The official ``mcp`` SDK is a core dependency: the manager
drives a real ``ClientSession`` per server (see :mod:`dream.mcp._client`).
"""

from __future__ import annotations

from dream.mcp._admission import admit
from dream.mcp._allowlist import (
    AllowlistError,
    entry_to_config,
    parse_allowlist,
    read_allowlist,
)
from dream.mcp._client import (
    McpClientManager,
    McpServerNotConnectedError,
    SessionOpener,
    UnsupportedTransportError,
)
from dream.mcp._types import (
    AllowlistEntry,
    McpConnectionStatus,
    McpHttpServerConfig,
    McpResourceInfo,
    McpServerConfig,
    McpState,
    McpStdioServerConfig,
    McpToolInfo,
    McpTransport,
    McpWebSocketServerConfig,
)

__all__ = [
    "AllowlistEntry",
    "AllowlistError",
    "McpClientManager",
    "McpConnectionStatus",
    "McpHttpServerConfig",
    "McpResourceInfo",
    "McpServerConfig",
    "McpServerNotConnectedError",
    "McpState",
    "McpStdioServerConfig",
    "McpToolInfo",
    "McpTransport",
    "McpWebSocketServerConfig",
    "SessionOpener",
    "UnsupportedTransportError",
    "admit",
    "entry_to_config",
    "parse_allowlist",
    "read_allowlist",
]
