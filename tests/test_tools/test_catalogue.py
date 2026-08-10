"""Tool catalogue for the system-prompt brief."""

from __future__ import annotations

from pydantic import BaseModel

from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._catalogue import ToolCatalogue


class _EmptyInput(BaseModel):
    pass


class _NamedTool(BaseTool):
    name = "alpha_tool"
    description = "First line of alpha.\nSecond line ignored."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=1.0)
    input_model = _EmptyInput

    async def execute(self, input: dict[str, object], ctx: object) -> object:
        raise NotImplementedError


class _OtherTool(BaseTool):
    name = "zeta_tool"
    description = "Zeta does work."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=1.0)
    input_model = _EmptyInput

    async def execute(self, input: dict[str, object], ctx: object) -> object:
        raise NotImplementedError


def test_from_tools_none_when_empty() -> None:
    assert ToolCatalogue.from_tools(()) is None


def test_from_tools_sorts_and_renders_first_line_only() -> None:
    catalogue = ToolCatalogue.from_tools((_OtherTool(), _NamedTool()))
    assert catalogue is not None
    assert [entry.name for entry in catalogue] == ["alpha_tool", "zeta_tool"]
    rendered = catalogue.render()
    assert rendered.startswith("# Available tools\n")
    assert "- **alpha_tool** — First line of alpha." in rendered
    assert "Second line" not in rendered
    assert "- **zeta_tool** — Zeta does work." in rendered
