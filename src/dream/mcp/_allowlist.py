"""Parse ``.harness/mcp-allowlist.toml`` — the MCP admission authority (Spec 06).

The allowlist is self-sufficient to connect: each entry carries the endpoint and
transport, so ``entry_to_config`` turns it into the ``McpServerConfig`` the
client needs. The allowlist is the *authority* (#9); a separate declared-config
set is checked against it by ``_admission``.
"""

from __future__ import annotations

import shlex
import tomllib
from pathlib import Path
from typing import Any

from dream.mcp._types import (
    AllowlistEntry,
    McpHttpServerConfig,
    McpServerConfig,
    McpStdioServerConfig,
    McpWebSocketServerConfig,
)

_VALID_TRANSPORTS = ("stdio", "http", "ws")
_STDIO_SCHEME = "stdio://"


class AllowlistError(ValueError):
    """Raised when ``mcp-allowlist.toml`` is malformed."""


def parse_allowlist(text: str) -> list[AllowlistEntry]:
    """Parse the allowlist TOML body into entries."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise AllowlistError(f"invalid allowlist TOML: {exc}") from exc

    raw = data.get("mcp", [])
    if not isinstance(raw, list):
        raise AllowlistError("allowlist '[[mcp]]' must be an array of tables")

    entries: list[AllowlistEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            raise AllowlistError(f"each '[[mcp]]' must be a table, got {type(item).__name__}")
        entries.append(_entry_from_table(item))
    return entries


def read_allowlist(path: Path) -> list[AllowlistEntry]:
    """Read + parse the allowlist file; a missing file yields no entries."""
    if not path.is_file():
        return []
    return parse_allowlist(path.read_text(encoding="utf-8"))


def entry_to_config(entry: AllowlistEntry) -> McpServerConfig:
    """Build the connection config for ``entry`` from its endpoint + transport."""
    if entry.transport == "stdio":
        body = entry.endpoint.removeprefix(_STDIO_SCHEME)
        parts = shlex.split(body)
        if not parts:
            raise AllowlistError(f"stdio endpoint has no command: {entry.endpoint!r}")
        return McpStdioServerConfig(command=parts[0], args=parts[1:])
    if entry.transport == "http":
        return McpHttpServerConfig(url=entry.endpoint)
    return McpWebSocketServerConfig(url=entry.endpoint)


def _entry_from_table(item: dict[str, Any]) -> AllowlistEntry:
    name = item.get("name")
    endpoint = item.get("endpoint")
    transport = item.get("transport")
    if not (isinstance(name, str) and name.strip()):
        raise AllowlistError(f"mcp entry missing 'name': {item!r}")
    if not (isinstance(endpoint, str) and endpoint.strip()):
        raise AllowlistError(f"mcp entry {name!r} missing 'endpoint'")
    if transport not in _VALID_TRANSPORTS:
        raise AllowlistError(
            f"mcp entry {name!r} has invalid transport {transport!r}; "
            f"expected one of {_VALID_TRANSPORTS}"
        )
    tools = item.get("tools", [])
    if not isinstance(tools, list):
        raise AllowlistError(f"mcp entry {name!r} 'tools' must be a list")
    pin = item.get("pinned_version_hash")
    return AllowlistEntry(
        name=name,
        endpoint=endpoint,
        transport=transport,
        tier_required=str(item.get("tier_required", "")),
        pinned_version_hash=pin if isinstance(pin, str) and pin.strip() else None,
        tools=tuple(str(t) for t in tools),
    )


__all__ = ["AllowlistError", "entry_to_config", "parse_allowlist", "read_allowlist"]
