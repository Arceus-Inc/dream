"""MCP configuration + runtime data shapes (Spec 06).

These are dream's *own* types — deliberately decoupled from the optional
``mcp`` SDK so importing this module never requires the extra. Shapes are
adapted from the OpenHarness reference (``mcp/types.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

McpTransport = Literal["stdio", "http", "ws"]
McpState = Literal["connected", "failed", "pending", "disabled"]


# --- connection config (what the SDK needs to connect) ----------------------


class McpStdioServerConfig(BaseModel):
    """A stdio MCP server: spawn ``command`` + ``args``."""

    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


class McpHttpServerConfig(BaseModel):
    """An HTTP (streamable) MCP server."""

    type: Literal["http"] = "http"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


class McpWebSocketServerConfig(BaseModel):
    """A WebSocket MCP server (best-effort in the reference build)."""

    type: Literal["ws"] = "ws"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


McpServerConfig = McpStdioServerConfig | McpHttpServerConfig | McpWebSocketServerConfig


# --- allowlist entry (the authority: .harness/mcp-allowlist.toml) -----------


@dataclass(frozen=True)
class AllowlistEntry:
    """One ``[[mcp]]`` entry — what *may* connect this session (Spec 06 #9-10).

    ``tools`` is the per-server tool coverage: when non-empty only those tool
    names are admitted (undeclared ones denied, #14); empty means the operator
    did not narrow and every advertised tool is admitted. ``tier_required`` is
    the #08 tier *name* (a string); the int the tool declaration needs is
    derived until #08's taxonomy lands.
    """

    name: str
    endpoint: str
    transport: McpTransport
    tier_required: str = ""
    pinned_version_hash: str | None = None
    tools: tuple[str, ...] = ()


# --- runtime info exposed by a connected server -----------------------------


@dataclass(frozen=True)
class McpToolInfo:
    """A tool advertised by a connected MCP server."""

    server_name: str
    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True)
class McpResourceInfo:
    """A resource advertised by a connected MCP server."""

    server_name: str
    name: str
    uri: str
    description: str = ""


@dataclass
class McpConnectionStatus:
    """Per-server runtime status (the ``pending→connected/failed/disabled`` SM)."""

    name: str
    state: McpState
    detail: str = ""
    transport: str = "unknown"
    auth_configured: bool = False
    tools: list[McpToolInfo] = field(default_factory=list)
    resources: list[McpResourceInfo] = field(default_factory=list)


__all__ = [
    "AllowlistEntry",
    "McpConnectionStatus",
    "McpHttpServerConfig",
    "McpResourceInfo",
    "McpServerConfig",
    "McpState",
    "McpStdioServerConfig",
    "McpToolInfo",
    "McpTransport",
    "McpWebSocketServerConfig",
]
