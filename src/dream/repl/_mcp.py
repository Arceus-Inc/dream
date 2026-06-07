"""REPL-side MCP session wiring (Spec 06 slice 4).

One async step turns the per-repo allowlist into live, registered tools:

    read allowlist → admit → connect → register tools → (gate on blocking)

It is deliberately separate from the REPL loop and takes an optional
``session_opener`` so it can be driven over the in-memory transport in tests
without spawning real servers. ``run_session_repl`` calls it with the default
(real) opener inside the event loop, then closes the manager on exit.

Admission note: dream has no separate MCP *config* source yet (plugins/#13 will
add one), so "configured" == the allowlist itself and ``admit`` cannot refuse
anything here today; the gate is wired for when external config arrives, and the
meaningful refusal is unit-tested in ``tests/test_mcp/test_admission.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dream.mcp import (
    AllowlistError,
    McpClientManager,
    SessionOpener,
    admit,
    read_allowlist,
)
from dream.services.repo_validator import Finding, has_blocking
from dream.tools._registry import ToolRegistry
from dream.tools.builtin.mcp_tool import register_mcp_management_tools, register_mcp_tools

ALLOWLIST_RELPATH = Path(".harness") / "mcp-allowlist.toml"
CREDENTIALS_RELPATH = Path(".harness") / "mcp-credentials.toml"


@dataclass(frozen=True)
class McpSetup:
    """Outcome of MCP session setup.

    ``manager`` is the live connection manager (``None`` when MCP is absent or a
    blocking finding aborted setup); ``findings`` carries admission/connect
    findings (blocking ones must stop the session); ``registered`` lists the tool
    names added to the registry.
    """

    manager: McpClientManager | None
    findings: list[Finding]
    registered: tuple[str, ...]


def mcp_paths(working_dir: Path) -> tuple[Path, Path]:
    """Return ``(allowlist_path, credentials_path)`` for ``working_dir``."""
    return working_dir / ALLOWLIST_RELPATH, working_dir / CREDENTIALS_RELPATH


async def setup_mcp_session(
    registry: ToolRegistry,
    *,
    allowlist_path: Path,
    credentials_path: Path,
    session_opener: SessionOpener | None = None,
) -> McpSetup:
    """Admit, connect, and register MCP tools; never raises on bad input."""
    try:
        entries = read_allowlist(allowlist_path)
    except AllowlistError as exc:
        return McpSetup(None, [_malformed_finding(exc, allowlist_path)], ())

    if not entries:
        return McpSetup(None, [], ())

    admitted, findings = admit([e.name for e in entries], entries)
    if has_blocking(findings):
        return McpSetup(None, findings, ())

    manager = McpClientManager(
        admitted, credentials_path=credentials_path, session_opener=session_opener
    )
    connect_findings = await manager.connect_all()
    findings = findings + connect_findings
    if has_blocking(findings):
        await manager.close()
        return McpSetup(None, findings, ())

    registered = (
        *register_mcp_tools(registry, manager),
        *register_mcp_management_tools(registry, manager, credentials_path),
    )
    return McpSetup(manager, findings, registered)


def _malformed_finding(exc: AllowlistError, path: Path) -> Finding:
    return Finding(
        severity="blocking",
        code="mcp_allowlist_malformed",
        message=f"MCP allowlist is malformed: {exc}",
        path=str(path),
    )


__all__ = [
    "ALLOWLIST_RELPATH",
    "CREDENTIALS_RELPATH",
    "McpSetup",
    "mcp_paths",
    "setup_mcp_session",
]
