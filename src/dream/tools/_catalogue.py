"""Tool discovery catalogue for the session system prompt.

Lists every tool advertised on the request wire (builtin, per-repo, MCP)
so the brief matches the callable surface. Schemas still travel on the
provider ``tools`` parameter; this is name + one-line description only.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from dream.tools._base import BaseTool

__all__ = [
    "ToolCatalogue",
    "ToolCatalogueEntry",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCatalogueEntry:
    """One discoverable tool."""

    name: str
    description: str

    def render_line(self) -> str:
        return f"- **{self.name}** — {self.description}"


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCatalogue:
    """Ordered catalogue rendered into ``<context>`` for the advertised tool set."""

    entries: tuple[ToolCatalogueEntry, ...]

    def __iter__(self) -> Iterator[ToolCatalogueEntry]:
        return iter(self.entries)

    @classmethod
    def from_tools(cls, tools: Sequence[BaseTool]) -> ToolCatalogue | None:
        """Build a catalogue from tools already filtered for model advertisement."""
        if not tools:
            return None
        ordered = sorted(tools, key=lambda tool: tool.name)
        return cls(
            entries=tuple(
                ToolCatalogueEntry(
                    name=tool.name,
                    description=_first_line(tool.description),
                )
                for tool in ordered
            )
        )

    def render(self) -> str:
        lines = ["# Available tools", ""]
        lines.extend(entry.render_line() for entry in self.entries)
        return "\n".join(lines)


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()
