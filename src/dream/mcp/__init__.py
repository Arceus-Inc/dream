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
from dream.mcp._credentials import (
    CredentialMode,
    CredentialsError,
    ServerCredential,
    apply_credentials,
    read_credentials,
    write_credential,
)
from dream.mcp._setup import (
    ALLOWLIST_RELPATH,
    CREDENTIALS_RELPATH,
    McpSetup,
    mcp_paths,
    setup_mcp_session,
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
    "ALLOWLIST_RELPATH",
    "CREDENTIALS_RELPATH",
    "AllowlistEntry",
    "AllowlistError",
    "CredentialMode",
    "CredentialsError",
    "McpClientManager",
    "McpConnectionStatus",
    "McpHttpServerConfig",
    "McpResourceInfo",
    "McpServerConfig",
    "McpServerNotConnectedError",
    "McpSetup",
    "McpState",
    "McpStdioServerConfig",
    "McpToolInfo",
    "McpTransport",
    "McpWebSocketServerConfig",
    "ServerCredential",
    "SessionOpener",
    "UnsupportedTransportError",
    "admit",
    "apply_credentials",
    "entry_to_config",
    "mcp_paths",
    "parse_allowlist",
    "read_allowlist",
    "read_credentials",
    "setup_mcp_session",
    "write_credential",
]
