"""Spec 06 slice 4 — mcp_auth persists a credential then reconnects (#auth flow).

Secrets are written to the gitignored credentials file (never the VCS allowlist);
the manager reconnects so the new secret takes effect.
"""

from __future__ import annotations

from pathlib import Path

from dream.mcp._client import McpClientManager
from dream.mcp._credentials import read_credentials
from dream.mcp._types import AllowlistEntry, McpConnectionStatus, McpTransport
from dream.services.repo_validator import Finding
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.mcp_auth import McpAuthTool
from tests.test_mcp._fakes import build_server, opener_for


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s")


def _entry(name: str, transport: McpTransport = "stdio") -> AllowlistEntry:
    return AllowlistEntry(name=name, endpoint=f"{transport}://{name}", transport=transport)


def test_mcp_auth_is_mutating() -> None:
    tool = McpAuthTool(McpClientManager([]), Path("x"))
    assert tool.is_read_only() is False


async def test_unknown_server_is_error(tmp_path: Path) -> None:
    mgr = McpClientManager([_entry("pw")], session_opener=opener_for({"pw": build_server("pw")}))
    await mgr.connect_all()
    tool = McpAuthTool(mgr, tmp_path / "creds.toml")
    result = await tool.execute(
        {"server_name": "other", "mode": "bearer", "value": "t"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata
    await mgr.close()


async def test_stdio_rejects_header_mode(tmp_path: Path) -> None:
    mgr = McpClientManager([_entry("pw")], session_opener=opener_for({"pw": build_server("pw")}))
    await mgr.connect_all()
    tool = McpAuthTool(mgr, tmp_path / "creds.toml")
    result = await tool.execute(
        {"server_name": "pw", "mode": "header", "value": "t"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert not (tmp_path / "creds.toml").exists()  # nothing persisted on rejection
    await mgr.close()


async def test_http_rejects_env_mode(tmp_path: Path) -> None:
    mgr = McpClientManager(
        [_entry("api", "http")], session_opener=opener_for({"api": build_server("api")})
    )
    await mgr.connect_all()
    tool = McpAuthTool(mgr, tmp_path / "creds.toml")
    result = await tool.execute(
        {"server_name": "api", "mode": "env", "value": "t"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    await mgr.close()


async def test_ws_is_rejected_and_not_persisted(tmp_path: Path) -> None:
    creds = tmp_path / "creds.toml"
    mgr = McpClientManager(
        [_entry("rt", "ws")], session_opener=opener_for({"rt": build_server("rt")})
    )
    await mgr.connect_all()
    tool = McpAuthTool(mgr, creds)
    result = await tool.execute(
        {"server_name": "rt", "mode": "bearer", "value": "t"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert not creds.exists()  # ws can't carry headers -> nothing persisted
    await mgr.close()


async def test_malformed_existing_creds_file_is_tool_error(tmp_path: Path) -> None:
    creds = tmp_path / "creds.toml"
    creds.write_text("this = = not toml", encoding="utf-8")
    mgr = McpClientManager([_entry("pw")], session_opener=opener_for({"pw": build_server("pw")}))
    await mgr.connect_all()
    tool = McpAuthTool(mgr, creds)
    result = await tool.execute(
        {"server_name": "pw", "mode": "bearer", "value": "t"}, _ctx(tmp_path)
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata
    await mgr.close()


async def test_blocking_reconnect_finding_fails_the_tool(tmp_path: Path) -> None:
    """A blocking finding from reconnect_all (e.g. a version-pin mismatch on a
    *different* allowlisted server) must fail mcp_auth — it cannot report
    success and silently drop a security-relevant reconnect failure."""
    creds = tmp_path / "creds.toml"
    mgr = _ReconnectFindingManager(
        entry=_entry("pw"),
        findings=[
            Finding(
                severity="blocking",
                code="mcp_version_mismatch",
                message="MCP version mismatch for 'other'",
            )
        ],
    )
    tool = McpAuthTool(mgr, creds)  # type: ignore[arg-type]
    result = await tool.execute(
        {"server_name": "pw", "mode": "bearer", "value": "s3cret"},
        _ctx(tmp_path),
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata
    assert "mcp_version_mismatch" in result.metadata["root_cause"]
    # The credential is still persisted (the write precedes reconnect); the
    # failure is about the reconnect, not the write.
    assert read_credentials(creds)["pw"].value == "s3cret"
    assert mgr.reconnect_calls == 1


async def test_non_blocking_reconnect_finding_does_not_fail(tmp_path: Path) -> None:
    """Advisory (warning/info) reconnect findings must not fail an otherwise
    successful auth — only blocking findings gate the tool."""
    creds = tmp_path / "creds.toml"
    mgr = _ReconnectFindingManager(
        entry=_entry("pw"),
        findings=[Finding(severity="warning", code="slow", message="slow handshake")],
    )
    tool = McpAuthTool(mgr, creds)  # type: ignore[arg-type]
    result = await tool.execute(
        {"server_name": "pw", "mode": "bearer", "value": "s3cret"},
        _ctx(tmp_path),
    )
    assert result.is_error is False


class _ReconnectFindingManager:
    """Manager stub: persists nothing, returns canned findings from reconnect."""

    def __init__(self, *, entry: AllowlistEntry, findings: list[Finding]) -> None:
        self._entry = entry
        self._findings = findings
        self.reconnect_calls = 0

    def entry_for(self, name: str) -> AllowlistEntry | None:
        return self._entry if name == self._entry.name else None

    async def reconnect_all(self) -> list[Finding]:
        self.reconnect_calls += 1
        return list(self._findings)

    def status(self, name: str) -> McpConnectionStatus | None:
        return McpConnectionStatus(name=name, state="connected", transport="stdio")


async def test_success_persists_and_reconnects(tmp_path: Path) -> None:
    creds = tmp_path / "creds.toml"
    mgr = McpClientManager([_entry("pw")], session_opener=opener_for({"pw": build_server("pw")}))
    await mgr.connect_all()
    tool = McpAuthTool(mgr, creds)
    result = await tool.execute(
        {"server_name": "pw", "mode": "bearer", "value": "s3cret", "key": "GH_TOKEN"},
        _ctx(tmp_path),
    )
    assert result.is_error is False
    stored = read_credentials(creds)["pw"]
    assert stored.mode == "bearer"
    assert stored.value == "s3cret"
    assert stored.key == "GH_TOKEN"
    status = mgr.status("pw")
    assert status is not None and status.state == "connected"
    await mgr.close()
