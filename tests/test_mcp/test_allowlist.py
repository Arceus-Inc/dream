"""Spec 06 — parse .harness/mcp-allowlist.toml + endpoint->config."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.mcp._allowlist import (
    AllowlistError,
    entry_to_config,
    parse_allowlist,
    read_allowlist,
)
from dream.mcp._types import McpHttpServerConfig, McpStdioServerConfig

_TOML = """
[[mcp]]
name = "playwright"
endpoint = "stdio://npx -y @playwright/mcp"
transport = "stdio"
tier_required = "repo-write+net-allowlist"
pinned_version_hash = "sha256:abc"
tools = ["navigate", "click"]

[[mcp]]
name = "remote"
endpoint = "https://mcp.example.com/v1"
transport = "http"
"""


def test_parse_basic_entries() -> None:
    entries = parse_allowlist(_TOML)
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {"playwright", "remote"}
    pw = by_name["playwright"]
    assert pw.transport == "stdio"
    assert pw.tier_required == "repo-write+net-allowlist"
    assert pw.pinned_version_hash == "sha256:abc"
    assert pw.tools == ("navigate", "click")
    assert by_name["remote"].pinned_version_hash is None
    assert by_name["remote"].tools == ()


def test_entry_to_config_stdio_parses_command_and_args() -> None:
    entry = parse_allowlist(_TOML)[0]
    config = entry_to_config(entry)
    assert isinstance(config, McpStdioServerConfig)
    assert config.command == "npx"
    assert config.args == ["-y", "@playwright/mcp"]


def test_entry_to_config_http_uses_url() -> None:
    remote = {e.name: e for e in parse_allowlist(_TOML)}["remote"]
    config = entry_to_config(remote)
    assert isinstance(config, McpHttpServerConfig)
    assert config.url == "https://mcp.example.com/v1"


def test_read_allowlist_missing_returns_empty(tmp_path: Path) -> None:
    assert read_allowlist(tmp_path / "nope.toml") == []


def test_malformed_toml_raises(tmp_path: Path) -> None:
    with pytest.raises(AllowlistError):
        parse_allowlist("this is not = valid = toml [[")
