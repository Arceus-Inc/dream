"""Spec 06 — McpToolAdapter rides the BaseTool contract; registration + order."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.mcp._client import McpClientManager
from dream.mcp._types import AllowlistEntry, McpToolInfo
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin import default_registry
from dream.tools.builtin.mcp_tool import (
    McpToolAdapter,
    input_model_from_schema,
    mcp_tool_name,
    register_mcp_tools,
)
from tests.test_mcp._fakes import build_server, opener_for


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s")


def _navigate_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {"url": {"type": "string"}, "wait": {"type": "boolean"}},
        "required": ["url"],
    }


async def _connected_manager() -> McpClientManager:
    server = build_server("pw", tool_names=("navigate",))
    entry = AllowlistEntry(name="pw", endpoint="stdio://pw", transport="stdio")
    mgr = McpClientManager([entry], session_opener=opener_for({"pw": server}))
    await mgr.connect_all()
    return mgr


def test_mcp_tool_name_namespaces_and_sanitizes() -> None:
    assert mcp_tool_name("playwright", "navigate") == "mcp__playwright__navigate"
    assert mcp_tool_name("weird name", "do/it") == "mcp__weird_name__do_it"


def test_input_model_from_schema_required_and_optional() -> None:
    model = input_model_from_schema("Navigate", _navigate_schema())
    model(url="x")  # required satisfied
    with pytest.raises(Exception):
        model()  # missing required url
    fields = model.model_fields
    assert "url" in fields and "wait" in fields


def test_adapter_name_schema_and_declaration() -> None:
    info = McpToolInfo(server_name="pw", name="navigate", description="Go", input_schema=_navigate_schema())
    adapter = McpToolAdapter(_DummyManager(), info, tier_required=1)
    assert adapter.name == "mcp__pw__navigate"
    assert adapter.declaration.risk == "mutating"
    assert adapter.declaration.tier_required == 1
    assert "url" in adapter.input_schema()["properties"]


async def test_adapter_execute_calls_manager(tmp_path: Path) -> None:
    mgr = await _connected_manager()
    info = mgr.list_tools()[0]
    adapter = McpToolAdapter(mgr, info)
    result = await adapter.execute({"url": "x"}, _ctx(tmp_path))
    assert result.is_error is False
    assert "navigate" in result.content


async def test_adapter_execute_disconnected_returns_tool_error(tmp_path: Path) -> None:
    # manager with the server *failed* (no fake session) -> call raises -> is_error
    entry = AllowlistEntry(name="pw", endpoint="stdio://pw", transport="stdio")
    mgr = McpClientManager([entry], session_opener=opener_for({}))
    await mgr.connect_all()
    info = McpToolInfo(server_name="pw", name="navigate", description="", input_schema={})
    adapter = McpToolAdapter(mgr, info)
    result = await adapter.execute({}, _ctx(tmp_path))
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_register_mcp_tools_uses_mcp_source_and_deterministic_order(tmp_path: Path) -> None:
    mgr = await _connected_manager()
    registry = default_registry()
    added = register_mcp_tools(registry, mgr)
    assert added == ["mcp__pw__navigate"]
    assert "mcp__pw__navigate" in registry
    # MCP tools come after the default bucket in the deterministic order.
    names = [t.name for t in registry.list_tools()]
    assert names[-1] == "mcp__pw__navigate"


class _DummyManager:
    """Minimal stand-in where the adapter only needs identity, not calls."""
