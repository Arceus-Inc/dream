"""Adapt an MCP server's tools onto the ``#05`` BaseTool contract (Spec 06).

Each admitted MCP tool becomes a ``BaseTool`` named ``mcp__{server}__{tool}``
with an ``input_model`` synthesized from the server's advertised JSON Schema,
registered under ``ToolSource.MCP`` so it slots into the deterministic order.

dream's ``BaseTool`` requires class-level ``name``/``description``/
``declaration``/``input_model`` (its ``__init_subclass__`` validation), so the
adapter declares placeholders at class scope and overrides them per instance —
a small divergence from the OpenHarness reference whose BaseTool is lenient.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, create_model

from dream.contracts.tool import ToolResult
from dream.mcp._client import McpClientManager, McpServerNotConnectedError
from dream.mcp._types import McpToolInfo
from dream.tools._base import BaseTool, RiskClass, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource
from dream.tools.builtin._mcp_effects import endpoint_host, tier_required_for

# MCP tools are external — default to the conservative non-safe risk class.
_MCP_RISK: RiskClass = "mutating"
# MCP tools are tier-gated above tier 0; the precise #08 tier-name -> int
# mapping lands with #08. Until then any allowlist tier means "not tier 0".
_MCP_TIER = 1

_JSON_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class _McpPlaceholderInput(BaseModel):
    """Placeholder input model so the class passes BaseTool validation."""


class McpToolAdapter(BaseTool):
    """One MCP tool exposed as a dream ``BaseTool``."""

    name = "mcp__placeholder"
    description = "An MCP-provided tool."
    declaration = ToolDeclaration(risk=_MCP_RISK, tier_required=_MCP_TIER, timeout_seconds=30.0)
    input_model: type[BaseModel] = _McpPlaceholderInput

    def __init__(
        self,
        manager: McpClientManager,
        tool_info: McpToolInfo,
        *,
        tier_required: int = _MCP_TIER,
    ) -> None:
        self._manager = manager
        self._tool_info = tool_info
        # Instance attrs shadow the class placeholders so each adapter carries
        # its own name / schema / tier while still being a valid ``BaseTool``.
        self.name = mcp_tool_name(tool_info.server_name, tool_info.name)
        self.description = tool_info.description or f"MCP tool {tool_info.name}"
        self.input_model = input_model_from_schema(self.name, tool_info.input_schema)
        self.declaration = ToolDeclaration(
            risk=_MCP_RISK, tier_required=tier_required, timeout_seconds=30.0
        )

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        """Report the server's host so the gate treats an MCP call as network.

        Without this the gate sees no effect and can't apply the NETWORK tier
        ceiling to an external tool. stdio (local subprocess) servers carry no
        host and report none; the gate then leans on the declared tier alone.
        """
        del input
        entry = self._manager.entry_for(self._tool_info.server_name)
        if entry is None:
            return ToolEffects()
        host = endpoint_host(entry.endpoint)
        return ToolEffects(network_host=host) if host is not None else ToolEffects()

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        del ctx
        try:
            output = await self._manager.call_tool(
                self._tool_info.server_name, self._tool_info.name, input
            )
        except McpServerNotConnectedError as exc:
            return ToolResult(
                content=f"MCP server {self._tool_info.server_name!r} is unavailable.",
                is_error=True,
                metadata={
                    "root_cause": str(exc),
                    "safe_retry": "wait for the server to reconnect, or run mcp_auth",
                    "stop_condition": "stop after repeated disconnects and escalate",
                },
            )
        return ToolResult(
            content=output,
            metadata={"server": self._tool_info.server_name, "tool": self._tool_info.name},
        )


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Build the namespaced tool name ``mcp__{server}__{tool}`` (sanitized)."""
    return f"mcp__{_sanitize_segment(server_name)}__{_sanitize_segment(tool_name)}"


def input_model_from_schema(model_name: str, schema: dict[str, object]) -> type[BaseModel]:
    """Synthesize a pydantic input model from an MCP tool's JSON Schema."""
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return create_model(_model_identifier(model_name))
    raw_required = schema.get("required", [])
    required = set(raw_required) if isinstance(raw_required, list) else set()
    fields: dict[str, Any] = {}
    for key, spec in properties.items():
        prop = spec if isinstance(spec, dict) else {}
        py_type = _JSON_TYPE_MAP.get(str(prop.get("type", "")), Any)
        if key in required:
            fields[key] = (py_type, ...)
        else:
            # "Not required" is not "nullable": JSON Schema only permits an
            # explicit null when the property's type allows it. Give optional
            # fields a default (so they may be omitted) WITHOUT widening the
            # type to ``| None``, unless the server schema declares null itself.
            field_type = (py_type | None) if _allows_null(prop) else py_type
            fields[key] = (field_type, None)
    return create_model(_model_identifier(model_name), **fields)


def _allows_null(prop: dict[str, Any]) -> bool:
    """Whether a property's JSON Schema explicitly permits ``null``."""
    declared = prop.get("type")
    if declared == "null":
        return True
    if isinstance(declared, list) and "null" in declared:
        return True
    for branch in _schema_branches(prop):
        if isinstance(branch, dict) and branch.get("type") == "null":
            return True
    return False


def _schema_branches(prop: dict[str, Any]) -> list[Any]:
    branches: list[Any] = []
    for key in ("anyOf", "oneOf"):
        value = prop.get(key)
        if isinstance(value, list):
            branches.extend(value)
    return branches


def register_mcp_tools(registry: ToolRegistry, manager: McpClientManager) -> list[str]:
    """Register every connected MCP tool as an adapter; return the names added.

    Each adapter's ``tier_required`` is derived from its server's allowlist
    entry (the operator's ``tier_required`` name), not hardcoded — so a server
    pinned to a higher tier is gated accordingly instead of every MCP tool
    landing at tier 1.
    """
    added: list[str] = []
    for info in manager.list_tools():
        entry = manager.entry_for(info.server_name)
        tier = tier_required_for(entry.tier_required) if entry is not None else _MCP_TIER
        adapter = McpToolAdapter(manager, info, tier_required=tier)
        registry.register(adapter, source=ToolSource.MCP)
        added.append(adapter.name)
    return added


def register_mcp_management_tools(
    registry: ToolRegistry, manager: McpClientManager, credentials_path: Path
) -> list[str]:
    """Register the MCP management tools (resources + auth); return names added.

    Registered under ``ToolSource.MCP`` so they share the deterministic MCP
    bucket with the per-server adapters. Imported lazily to avoid a module-load
    cycle (these tools import the manager which imports this module's siblings).
    """
    from dream.tools.builtin.mcp_auth import McpAuthTool
    from dream.tools.builtin.mcp_resources import (
        ListMcpResourcesTool,
        ReadMcpResourceTool,
    )

    tools: list[BaseTool] = [
        ListMcpResourcesTool(manager),
        ReadMcpResourceTool(manager),
        McpAuthTool(manager, credentials_path),
    ]
    for tool in tools:
        registry.register(tool, source=ToolSource.MCP)
    return [tool.name for tool in tools]


def _sanitize_segment(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", value)
    return sanitized or "tool"


def _model_identifier(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name) + "_input"


__all__ = [
    "McpToolAdapter",
    "input_model_from_schema",
    "mcp_tool_name",
    "register_mcp_management_tools",
    "register_mcp_tools",
]
