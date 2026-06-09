"""Spec 06 slice 4 — REPL MCP session setup (admit → connect → register → gate).

Exercised with an injected in-memory opener so no real servers are spawned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from dream.contracts.tool import ToolResult
from dream.repl._mcp import setup_mcp_session
from dream.services.repo_validator import has_blocking
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolSource
from dream.tools.builtin import default_registry
from tests.test_mcp._fakes import build_server, opener_for


class _StubInput(BaseModel):
    pass


class _CollidingTool(BaseTool):
    """A throwaway tool that squats on an MCP tool name to force a collision."""

    name = "mcp__pw__navigate"
    description = "squatter"
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=1.0)
    input_model = _StubInput

    async def execute(
        self, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:  # pragma: no cover - never invoked
        raise NotImplementedError


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


async def test_tool_name_collision_blocks_and_closes_manager(tmp_path: Path) -> None:
    """A registration collision (sanitised MCP name hits an existing tool) must
    become a blocking finding — never an escaping exception — and the manager
    opened during connect must be closed so no MCP session leaks."""
    allow = tmp_path / ".harness" / "mcp-allowlist.toml"
    _write_allowlist(allow, _entry_toml("pw"))
    registry = default_registry()
    # Squat on the name the ``pw`` server's ``navigate`` tool will register as,
    # so ``register_mcp_tools`` raises ToolCollisionError mid-setup.
    registry.register(_CollidingTool(), source=ToolSource.PER_REPO)
    server = build_server("pw", tool_names=("navigate",))

    result = await setup_mcp_session(
        registry,
        allowlist_path=allow,
        credentials_path=tmp_path / "creds.toml",
        session_opener=opener_for({"pw": server}),
    )

    # No exception escaped; the failure surfaced as a blocking finding.
    assert result.manager is None
    assert result.registered == ()
    assert has_blocking(result.findings)


async def test_malformed_allowlist_blocks(tmp_path: Path) -> None:
    allow = tmp_path / ".harness" / "mcp-allowlist.toml"
    _write_allowlist(allow, "this = = not toml")
    registry = default_registry()
    result = await setup_mcp_session(
        registry, allowlist_path=allow, credentials_path=tmp_path / "creds.toml"
    )
    assert result.manager is None
    assert has_blocking(result.findings)
