"""``ToolRegistry`` — name → tool with deterministic listing + collision rules.

Spec 05 acceptance criteria:

- Listing is deterministic across processes: defaults emit in the
  ``default_order`` canonical sequence (with any leftover defaults appended
  alphabetically), then per-repo tools alphabetically, then skill + MCP
  tools alphabetically. Stable order matters so the API schema sent to the
  model is byte-identical run-to-run, which lets cache / prompt-tuning
  layers downstream actually hit.
- A name collision (same name registered twice, even across sources) is
  a session-blocking error: ``ToolCollisionError`` is raised at
  ``register`` time so the harness refuses to start a session in an
  ambiguous state rather than picking a winner silently.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import Enum
from typing import Any

from dream.tools._base import BaseTool


class ToolSource(Enum):
    """Provenance tag controlling registry list-order and trust."""

    DEFAULT = "default"
    PER_REPO = "per_repo"
    SKILL = "skill"
    MCP = "mcp"

    @property
    def is_builtin(self) -> bool:
        """Whether this provenance is a vetted built-in tool.

        Only built-ins (``DEFAULT``) are trusted at their declared tier. The
        rest (per-repo, skill, MCP) are *discovered* and ride the trust ramp:
        untrusted until an operator promotes them in tool-tier-overrides.
        """
        return self is ToolSource.DEFAULT


class ToolCollisionError(ValueError):
    """A tool name was registered more than once."""


class ToolRegistry:
    """Map tool names to ``BaseTool`` instances with deterministic ordering."""

    def __init__(self, default_order: tuple[str, ...] = ()) -> None:
        self._default_order = default_order
        self._tools: dict[str, BaseTool] = {}
        self._sources: dict[str, ToolSource] = {}

    def register(self, tool: BaseTool, *, source: ToolSource) -> None:
        """Add ``tool`` to the registry under ``tool.name``.

        Raises ``ToolCollisionError`` if a tool with the same name is
        already registered (regardless of source).
        """
        name = tool.name
        if name in self._tools:
            prior = self._sources[name].value
            raise ToolCollisionError(
                f"tool name {name!r} already registered "
                f"(prior source: {prior}, new source: {source.value})"
            )
        self._tools[name] = tool
        self._sources[name] = source

    def get(self, name: str) -> BaseTool | None:
        """Return the registered tool, or ``None`` if not present."""
        return self._tools.get(name)

    def iter_with_source(self) -> Iterator[tuple[BaseTool, ToolSource]]:
        """Yield ``(tool, source)`` pairs in deterministic listing order.

        Carries each tool's provenance alongside it so callers that gate on
        trust (e.g. the permission-gate builder) need not re-derive origin.
        """
        for tool in self.list_tools():
            yield tool, self._sources[tool.name]

    def list_tools(self) -> list[BaseTool]:
        """Return all registered tools in deterministic order."""
        by_source: dict[ToolSource, list[str]] = {s: [] for s in ToolSource}
        for name, src in self._sources.items():
            by_source[src].append(name)

        ordered_names: list[str] = []
        # Defaults: canonical first, then leftover defaults alphabetically.
        default_names = set(by_source[ToolSource.DEFAULT])
        for canon in self._default_order:
            if canon in default_names:
                ordered_names.append(canon)
                default_names.discard(canon)
        ordered_names.extend(sorted(default_names))
        # Per-repo, skill, MCP: alphabetical within each bucket.
        ordered_names.extend(sorted(by_source[ToolSource.PER_REPO]))
        ordered_names.extend(sorted(by_source[ToolSource.SKILL]))
        ordered_names.extend(sorted(by_source[ToolSource.MCP]))
        return [self._tools[n] for n in ordered_names]

    def to_api_schema(self) -> list[dict[str, Any]]:
        """Return all tool schemas in the same order as ``list_tools``."""
        return [t.to_api_schema() for t in self.list_tools()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def __iter__(self) -> Iterator[BaseTool]:
        return iter(self.list_tools())


__all__ = [
    "ToolCollisionError",
    "ToolRegistry",
    "ToolSource",
]
