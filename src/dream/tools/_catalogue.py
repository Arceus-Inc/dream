"""Tool discovery catalogues for the session system prompt.

Cursor-shaped slices:

- ``# Tool definitions`` — builtin (``ToolSource.DEFAULT``) tools
- ``# MCP & dynamic tools`` — per-repo, skill, and MCP tools

Schemas still travel on the provider ``tools`` parameter; this is name +
one-line description only so the brief matches the pie categories.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from dream.tools._base import BaseTool
from dream.tools._registry import ToolSource

__all__ = [
    "ToolCatalogue",
    "ToolCatalogueEntry",
]

_TOOL_DEFINITIONS_HEADER = "# Tool definitions"
_MCP_DYNAMIC_HEADER = "# MCP & dynamic tools"


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCatalogueEntry:
    """One discoverable tool in the brief."""

    name: str
    description: str
    source: ToolSource

    def render_line(self) -> str:
        return f"- **{self.name}** — {self.description}"

    @property
    def is_builtin(self) -> bool:
        return self.source.is_builtin


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCatalogue:
    """Ordered catalogues for builtin vs MCP/dynamic tools."""

    tool_definitions: tuple[ToolCatalogueEntry, ...]
    mcp_and_dynamic: tuple[ToolCatalogueEntry, ...]

    def __iter__(self) -> Iterator[ToolCatalogueEntry]:
        yield from self.tool_definitions
        yield from self.mcp_and_dynamic

    @classmethod
    def from_sourced(
        cls,
        tools: Sequence[tuple[BaseTool, ToolSource]],
    ) -> ToolCatalogue | None:
        """Build catalogues from tools already filtered for model advertisement."""
        if not tools:
            return None
        builtins: list[ToolCatalogueEntry] = []
        dynamic: list[ToolCatalogueEntry] = []
        for tool, source in tools:
            entry = ToolCatalogueEntry(
                name=tool.name,
                description=_first_line(tool.description),
                source=source,
            )
            if entry.is_builtin:
                builtins.append(entry)
            else:
                dynamic.append(entry)
        builtins.sort(key=lambda entry: entry.name)
        dynamic.sort(key=lambda entry: entry.name)
        catalogue = cls(
            tool_definitions=tuple(builtins),
            mcp_and_dynamic=tuple(dynamic),
        )
        if not catalogue.tool_definitions and not catalogue.mcp_and_dynamic:
            return None
        return catalogue

    def render(self) -> str:
        parts: list[str] = []
        if self.tool_definitions:
            parts.append(
                "\n".join(
                    (
                        _TOOL_DEFINITIONS_HEADER,
                        "",
                        *(entry.render_line() for entry in self.tool_definitions),
                    )
                )
            )
        if self.mcp_and_dynamic:
            parts.append(
                "\n".join(
                    (
                        _MCP_DYNAMIC_HEADER,
                        "",
                        *(entry.render_line() for entry in self.mcp_and_dynamic),
                    )
                )
            )
        return "\n\n".join(parts)


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()
