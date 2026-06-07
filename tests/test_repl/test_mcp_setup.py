"""Spec 06 slice 4 — REPL MCP session setup (admit → connect → register → gate).

Exercised with an injected in-memory opener so no real servers are spawned.
"""

from __future__ import annotations

from pathlib import Path

from dream.repl._mcp import setup_mcp_session
from dream.services.repo_validator import has_blocking
from dream.tools.builtin import default_registry
from tests.test_mcp._fakes import build_server, opener_for


def _write_allowlist(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _entry_toml(name: str, *, pin: str | None = None) -> str:
    pin_line = f'pinned_version_hash = "{pin}"\n' if pin else ""
    return f'[[mcp]]\nname = "{name}"\nendpoint = "stdio://{name}"\ntransport = "stdio"\n{pin_line}'


async def test_no_allowlist_registers_nothing(tmp_path: Path) -> None:
    registry = default_registry()
    before = {t.name for t in registry.list_tools()}
    result = await setup_mcp_session(
        registry,
        allowlist_path=tmp_path / "missing.toml",
        credentials_path=tmp_path / "creds.toml",
    )
    assert result.manager is None
    assert result.findings == []
    assert {t.name for t in registry.list_tools()} == before


async def test_listed_server_connects_and_registers(tmp_path: Path) -> None:
    allow = tmp_path / ".harness" / "mcp-allowlist.toml"
    _write_allowlist(allow, _entry_toml("pw"))
    registry = default_registry()
    server = build_server("pw", tool_names=("navigate",))
    result = await setup_mcp_session(
        registry,
        allowlist_path=allow,
        credentials_path=tmp_path / "creds.toml",
        session_opener=opener_for({"pw": server}),
    )
    assert result.manager is not None
    names = {t.name for t in registry.list_tools()}
    assert "mcp__pw__navigate" in names
    assert {"list_mcp_resources", "read_mcp_resource", "mcp_auth"} <= names
    await result.manager.close()


async def test_pin_mismatch_blocks_and_registers_nothing(tmp_path: Path) -> None:
    allow = tmp_path / ".harness" / "mcp-allowlist.toml"
    _write_allowlist(allow, _entry_toml("pw", pin="sha256:deadbeef"))
    registry = default_registry()
    before = {t.name for t in registry.list_tools()}
    result = await setup_mcp_session(
        registry,
        allowlist_path=allow,
        credentials_path=tmp_path / "creds.toml",
        session_opener=opener_for({"pw": build_server("pw")}),
    )
    assert result.manager is None
    assert has_blocking(result.findings)
    assert {t.name for t in registry.list_tools()} == before


async def test_malformed_allowlist_blocks(tmp_path: Path) -> None:
    allow = tmp_path / ".harness" / "mcp-allowlist.toml"
    _write_allowlist(allow, "this = = not toml")
    registry = default_registry()
    result = await setup_mcp_session(
        registry, allowlist_path=allow, credentials_path=tmp_path / "creds.toml"
    )
    assert result.manager is None
    assert has_blocking(result.findings)
