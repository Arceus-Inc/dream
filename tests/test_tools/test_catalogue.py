"""Tool catalogues for the system-prompt brief (Cursor pie slices)."""

from __future__ import annotations

from pydantic import BaseModel

from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._catalogue import ToolCatalogue
from dream.tools._registry import ToolSource


class _EmptyInput(BaseModel):
    pass


class _BuiltinTool(BaseTool):
    name = "read_file"
    description = "Read a file.\nExtra detail ignored."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=1.0)
    input_model = _EmptyInput

    async def execute(self, input: dict[str, object], ctx: object) -> object:
        raise NotImplementedError


class _McpTool(BaseTool):
    name = "mcp_github__list"
    description = "List GitHub issues."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=1.0)
    input_model = _EmptyInput

    async def execute(self, input: dict[str, object], ctx: object) -> object:
        raise NotImplementedError


class _PerRepoTool(BaseTool):
    name = "repo_lint"
    description = "Lint the workspace."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=1.0)
    input_model = _EmptyInput

    async def execute(self, input: dict[str, object], ctx: object) -> object:
        raise NotImplementedError


def test_from_sourced_none_when_empty() -> None:
    assert ToolCatalogue.from_sourced(()) is None


def test_from_sourced_splits_builtin_vs_mcp_dynamic() -> None:
    catalogue = ToolCatalogue.from_sourced(
        (
            (_PerRepoTool(), ToolSource.PER_REPO),
            (_BuiltinTool(), ToolSource.DEFAULT),
            (_McpTool(), ToolSource.MCP),
        )
    )
    assert catalogue is not None
    assert [entry.name for entry in catalogue.tool_definitions] == ["read_file"]
    assert [entry.name for entry in catalogue.mcp_and_dynamic] == [
        "mcp_github__list",
        "repo_lint",
    ]
    rendered = catalogue.render()
    assert rendered.startswith("# Tool definitions\n")
    assert "# MCP & dynamic tools" in rendered
    assert "- **read_file** — Read a file." in rendered
    assert "Extra detail" not in rendered
    assert "- **mcp_github__list** — List GitHub issues." in rendered
    assert "- **repo_lint** — Lint the workspace." in rendered
    # Builtin section before MCP/dynamic.
    assert rendered.index("# Tool definitions") < rendered.index("# MCP & dynamic tools")


def test_mcp_only_omits_tool_definitions_header() -> None:
    catalogue = ToolCatalogue.from_sourced(((_McpTool(), ToolSource.MCP),))
    assert catalogue is not None
    rendered = catalogue.render()
    assert "# Tool definitions" not in rendered
    assert rendered.startswith("# MCP & dynamic tools\n")
