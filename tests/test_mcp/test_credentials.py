"""Spec 06 slice 4 — local (gitignored) MCP credentials layer.

Secrets live OUT of the VCS allowlist, in ``.harness/mcp-credentials.toml``;
``apply_credentials`` merges them into the connection config at connect time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.mcp._credentials import (
    CredentialsError,
    ServerCredential,
    apply_credentials,
    read_credentials,
    write_credential,
)
from dream.mcp._types import (
    McpHttpServerConfig,
    McpStdioServerConfig,
    McpWebSocketServerConfig,
)

# --- read -------------------------------------------------------------------


def test_read_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_credentials(tmp_path / "nope.toml") == {}


def test_read_parses_entries(tmp_path: Path) -> None:
    p = tmp_path / "creds.toml"
    p.write_text(
        '[playwright]\nmode = "bearer"\nvalue = "tok"\n\n'
        '[github]\nmode = "header"\nvalue = "v"\nkey = "X-Api-Key"\n',
        encoding="utf-8",
    )
    creds = read_credentials(p)
    assert creds["playwright"] == ServerCredential(mode="bearer", value="tok")
    assert creds["github"] == ServerCredential(mode="header", value="v", key="X-Api-Key")


def test_read_malformed_raises(tmp_path: Path) -> None:
    p = tmp_path / "creds.toml"
    p.write_text("not = = toml", encoding="utf-8")
    with pytest.raises(CredentialsError):
        read_credentials(p)


def test_read_invalid_mode_raises(tmp_path: Path) -> None:
    p = tmp_path / "creds.toml"
    p.write_text('[x]\nmode = "wat"\nvalue = "v"\n', encoding="utf-8")
    with pytest.raises(CredentialsError):
        read_credentials(p)


def test_read_missing_value_raises(tmp_path: Path) -> None:
    p = tmp_path / "creds.toml"
    p.write_text('[x]\nmode = "bearer"\n', encoding="utf-8")
    with pytest.raises(CredentialsError):
        read_credentials(p)


# --- write ------------------------------------------------------------------


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "creds.toml"
    write_credential(p, "pw", ServerCredential(mode="bearer", value="s3cret"))
    assert read_credentials(p) == {"pw": ServerCredential(mode="bearer", value="s3cret")}


def test_write_merges_preserving_others(tmp_path: Path) -> None:
    p = tmp_path / "creds.toml"
    write_credential(p, "a", ServerCredential(mode="env", value="1"))
    write_credential(p, "b", ServerCredential(mode="header", value="2", key="K"))
    creds = read_credentials(p)
    assert set(creds) == {"a", "b"}
    assert creds["a"].value == "1"


def test_write_overwrites_same_server(tmp_path: Path) -> None:
    p = tmp_path / "creds.toml"
    write_credential(p, "a", ServerCredential(mode="env", value="old"))
    write_credential(p, "a", ServerCredential(mode="env", value="new"))
    assert read_credentials(p)["a"].value == "new"


def test_write_uses_owner_only_permissions(tmp_path: Path) -> None:
    p = tmp_path / "creds.toml"
    write_credential(p, "a", ServerCredential(mode="env", value="1"))
    assert (p.stat().st_mode & 0o777) == 0o600


def test_write_roundtrips_special_characters(tmp_path: Path) -> None:
    p = tmp_path / "creds.toml"
    nasty = 'quote " back \\ tab \t end'
    write_credential(p, "weird name", ServerCredential(mode="header", value=nasty, key='a"b'))
    creds = read_credentials(p)
    assert creds["weird name"] == ServerCredential(mode="header", value=nasty, key='a"b')


def test_write_roundtrips_control_and_del_characters(tmp_path: Path) -> None:
    p = tmp_path / "creds.toml"
    nasty = "ctl\x01 del\x7f end"
    write_credential(p, "x", ServerCredential(mode="bearer", value=nasty))
    assert read_credentials(p)["x"].value == nasty


# --- apply ------------------------------------------------------------------


def test_apply_none_returns_same_config() -> None:
    cfg = McpStdioServerConfig(command="x")
    assert apply_credentials(cfg, None) is cfg


def test_apply_stdio_plain_env_default_key() -> None:
    out = apply_credentials(
        McpStdioServerConfig(command="x"), ServerCredential(mode="env", value="tok")
    )
    assert isinstance(out, McpStdioServerConfig)
    assert out.env == {"MCP_AUTH_TOKEN": "tok"}


def test_apply_stdio_bearer_prefixes() -> None:
    out = apply_credentials(
        McpStdioServerConfig(command="x"), ServerCredential(mode="bearer", value="tok")
    )
    assert isinstance(out, McpStdioServerConfig)
    assert out.env == {"MCP_AUTH_TOKEN": "Bearer tok"}


def test_apply_stdio_custom_key_preserves_existing_env() -> None:
    out = apply_credentials(
        McpStdioServerConfig(command="x", env={"PATH": "/bin"}),
        ServerCredential(mode="env", value="tok", key="GH_TOKEN"),
    )
    assert isinstance(out, McpStdioServerConfig)
    assert out.env == {"PATH": "/bin", "GH_TOKEN": "tok"}


def test_apply_http_header_default_authorization() -> None:
    out = apply_credentials(
        McpHttpServerConfig(url="https://x"), ServerCredential(mode="bearer", value="tok")
    )
    assert isinstance(out, McpHttpServerConfig)
    assert out.headers == {"Authorization": "Bearer tok"}


def test_apply_http_header_custom_key_plain() -> None:
    out = apply_credentials(
        McpHttpServerConfig(url="https://x"),
        ServerCredential(mode="header", value="k", key="X-Api-Key"),
    )
    assert isinstance(out, McpHttpServerConfig)
    assert out.headers == {"X-Api-Key": "k"}


def test_apply_ws_header() -> None:
    out = apply_credentials(
        McpWebSocketServerConfig(url="wss://x"),
        ServerCredential(mode="bearer", value="tok"),
    )
    assert isinstance(out, McpWebSocketServerConfig)
    assert out.headers == {"Authorization": "Bearer tok"}


def test_apply_does_not_mutate_original() -> None:
    cfg = McpStdioServerConfig(command="x")
    apply_credentials(cfg, ServerCredential(mode="env", value="tok"))
    assert cfg.env is None  # original untouched
