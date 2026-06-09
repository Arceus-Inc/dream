"""MCP resource tools — enumerate + read server resources on demand (Spec 06 #14).

Resources are *not* auto-injected into context (MUST #17); the agent pulls them
through these two read-only tools, the same progressive-disclosure spirit as
skills. Both take the session's :class:`McpClientManager` by constructor
injection — they are registered dynamically only when MCP is configured, so they
capture the live manager directly rather than reading it from execution context.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.mcp._client import McpClientManager, McpServerNotConnectedError
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error

_READ_ONLY = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=30.0)


class ListMcpResourcesInput(BaseModel):
    """No arguments — lists every resource across connected servers."""


class ReadMcpResourceInput(BaseModel):
    """Arguments for reading a single MCP resource."""

    server: str = Field(description="MCP server name that owns the resource.")
    uri: str = Field(description="Resource URI to read.")


class ListMcpResourcesTool(BaseTool):
    """List MCP resources discovered from connected servers."""

    name = "list_mcp_resources"
    description = "List MCP resources available from connected servers."
    declaration = _READ_ONLY
    input_model = ListMcpResourcesInput

    def __init__(self, manager: McpClientManager) -> None:
        self._manager = manager

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        del input, ctx
        resources = self._manager.list_resources()
        if not resources:
            return ToolResult(content="(no MCP resources)", metadata={"count": 0})
        lines = [
            f"{item.server_name}:{item.uri} {item.description}".rstrip() for item in resources
        ]
        return ToolResult(content="\n".join(lines), metadata={"count": len(resources)})


class ReadMcpResourceTool(BaseTool):
    """Read one resource from an MCP server by URI."""

    name = "read_mcp_resource"
    description = "Read an MCP resource by server name and URI."
    declaration = _READ_ONLY
    input_model = ReadMcpResourceInput

    def __init__(self, manager: McpClientManager) -> None:
        self._manager = manager

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        del ctx
        args = ReadMcpResourceInput.model_validate(input)
        try:
            output = await self._manager.read_resource(args.server, args.uri)
        except McpServerNotConnectedError as exc:
            return tool_error(
                f"MCP server {args.server!r} is unavailable.",
                root_cause=str(exc),
                safe_retry="wait for the server to reconnect, or run mcp_auth",
                stop_condition="stop after repeated disconnects and escalate",
            )
        return ToolResult(content=output, metadata={"server": args.server, "uri": args.uri})


__all__ = [
    "ListMcpResourcesInput",
    "ListMcpResourcesTool",
    "ReadMcpResourceInput",
    "ReadMcpResourceTool",
]
