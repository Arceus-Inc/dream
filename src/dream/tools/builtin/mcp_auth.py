"""``mcp_auth`` tool — configure an MCP server's credentials, then reconnect.

Diverges from the OpenHarness reference, which persists secrets into the same
``settings.json`` it loads server *config* from. dream keeps the connection
authority (``.harness/mcp-allowlist.toml``) under version control, so secrets
must not live beside it: this tool writes to the gitignored
``.harness/mcp-credentials.toml`` instead, then asks the manager to reconnect so
the new secret takes effect. The manager + credentials path are injected at
construction (the tool is registered only when MCP is configured).

Mode/transport pairing: stdio servers accept ``env`` or ``bearer`` (the secret
lands in an env var); http/ws servers accept ``header`` or ``bearer`` (the secret
lands in a request header). A mismatch is refused and nothing is persisted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.mcp._client import McpClientManager
from dream.mcp._credentials import (
    CredentialMode,
    CredentialsError,
    ServerCredential,
    write_credential,
)
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error as _err
from dream.tools.builtin._mcp_effects import endpoint_host

_STDIO_MODES: frozenset[CredentialMode] = frozenset({"env", "bearer"})
_NETWORK_MODES: frozenset[CredentialMode] = frozenset({"header", "bearer"})


class McpAuthInput(BaseModel):
    """Arguments for configuring an MCP server's auth."""

    server_name: str = Field(description="Allowlisted MCP server name.")
    mode: CredentialMode = Field(description="Auth mode: bearer, header, or env.")
    value: str = Field(description="Secret value to persist.")
    key: str | None = Field(
        default=None, description="Header name or env-var key override (optional)."
    )


class McpAuthTool(BaseTool):
    """Persist auth for one MCP server and reconnect active sessions."""

    name = "mcp_auth"
    description = "Configure auth for an MCP server and reconnect it."
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=30.0)
    input_model = McpAuthInput

    def __init__(self, manager: McpClientManager, credentials_path: Path) -> None:
        self._manager = manager
        self._credentials_path = credentials_path

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        """Report the target server's host so the gate treats auth as a network
        action (it reconnects the server). stdio servers are local and report no
        host; an unknown server name also yields no host (execute() rejects it)."""
        args = McpAuthInput.model_validate(input)
        entry = self._manager.entry_for(args.server_name)
        if entry is None:
            return ToolEffects()
        host = endpoint_host(entry.endpoint)
        return ToolEffects(network_host=host) if host is not None else ToolEffects()

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        del ctx
        args = McpAuthInput.model_validate(input)

        entry = self._manager.entry_for(args.server_name)
        if entry is None:
            return _err(
                f"Unknown MCP server: {args.server_name}",
                root_cause=f"{args.server_name!r} is not on the allowlist",
                safe_retry="use a server name from the allowlist",
                stop_condition="do not retry with an unlisted server",
            )

        # The SDK's ``websocket_client`` takes no headers, so a credential can't
        # be injected over ws — refuse rather than persist a secret and falsely
        # report success.
        if entry.transport == "ws":
            return _err(
                "WebSocket MCP servers can't take injected credentials in this build.",
                root_cause="the MCP SDK's websocket_client accepts no auth headers",
                safe_retry="use a stdio or http server for credentialed auth",
                stop_condition="do not retry auth against a ws server",
            )

        allowed = _STDIO_MODES if entry.transport == "stdio" else _NETWORK_MODES
        if args.mode not in allowed:
            return _err(
                f"{entry.transport} MCP auth supports {sorted(allowed)} modes, not {args.mode!r}.",
                root_cause=f"mode {args.mode!r} is invalid for a {entry.transport} server",
                safe_retry=f"retry with one of {sorted(allowed)}",
                stop_condition="do not retry with an incompatible mode",
            )

        try:
            write_credential(
                self._credentials_path,
                args.server_name,
                ServerCredential(mode=args.mode, value=args.value, key=args.key),
            )
        except CredentialsError as exc:
            return _err(
                f"Failed to persist credential for {args.server_name!r}: {exc}",
                root_cause=str(exc),
                safe_retry="fix or delete the credentials file, then retry",
                stop_condition="stop after repeated write failures and escalate",
            )
        findings = await self._manager.reconnect_all()
        blocking = [f for f in findings if f.severity == "blocking"]
        if blocking:
            detail = "; ".join(f"{f.code}: {f.message}" for f in blocking)
            return _err(
                f"Saved MCP auth for {args.server_name!r}, but reconnect found "
                f"blocking issue(s): {detail}.",
                root_cause=f"blocking reconnect finding(s): {detail}",
                safe_retry="resolve the reported issue (e.g. version pin) and retry mcp_auth",
                stop_condition="do not retry until the blocking reconnect finding clears",
            )

        status = self._manager.status(args.server_name)
        state = status.state if status is not None else "unknown"
        if state == "connected":
            return ToolResult(
                content=f"Saved MCP auth for {args.server_name!r}; reconnected.",
                metadata={"server": args.server_name, "state": state},
            )
        detail = status.detail if status is not None else "no status"
        return _err(
            f"Saved MCP auth for {args.server_name!r}, but the server is {state}.",
            root_cause=detail or f"server is {state} after reconnect",
            safe_retry="check the credential value/key and retry mcp_auth",
            stop_condition="stop after repeated auth failures and escalate",
        )


__all__ = ["McpAuthInput", "McpAuthTool"]
