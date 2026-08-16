"""Subagent discovery catalogue for the session system prompt.

Usage policy lives in Dream standing orders. This module only lists the
templates available to the beat — same role as the skill catalogue.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from dream.subagents._builtins import EXPLORE, PLAN, VERIFY, merge_builtins
from dream.subagents._declaration import (
    GENERAL_PURPOSE_DESCRIPTION,
    GENERAL_PURPOSE_NAME,
    Subagent,
    SubagentSet,
)

__all__ = [
    "SubagentCatalogue",
    "SubagentCatalogueEntry",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class SubagentCatalogueEntry:
    """One discoverable subagent template."""

    name: str
    description: str

    def render_line(self) -> str:
        return f"- **{self.name}** — {self.description}"


@dataclass(frozen=True, slots=True, kw_only=True)
class SubagentCatalogue:
    """Ordered catalogue rendered into ``<context>`` when spawn is enabled."""

    entries: tuple[SubagentCatalogueEntry, ...]

    def __iter__(self) -> Iterator[SubagentCatalogueEntry]:
        return iter(self.entries)

    @classmethod
    def for_set(cls, subagent_set: SubagentSet | None) -> SubagentCatalogue | None:
        """Build a catalogue, or ``None`` when spawn is not wired on the harness.

        Entries match the live resolver: ``generalPurpose``, then ``explore`` /
        ``plan`` / ``verify`` (role overrides win), then remaining role names.
        """
        if subagent_set is None:
            return None
        merged = merge_builtins(subagent_set)
        general = SubagentCatalogueEntry(
            name=GENERAL_PURPOSE_NAME,
            description=GENERAL_PURPOSE_DESCRIPTION,
        )
        reserved = {GENERAL_PURPOSE_NAME, EXPLORE, PLAN, VERIFY}
        ordered: list[SubagentCatalogueEntry] = []
        for name in (EXPLORE, PLAN, VERIFY):
            agent = merged.get(name)
            if agent is not None:
                ordered.append(_entry_for(agent))
        ordered.extend(_entry_for(agent) for agent in merged if agent.name not in reserved)
        return cls(entries=(general, *ordered))

    def render(self) -> str:
        lines = ["# Subagent definitions", ""]
        lines.extend(entry.render_line() for entry in self.entries)
        return "\n".join(lines)


def _entry_for(agent: Subagent) -> SubagentCatalogueEntry:
    return SubagentCatalogueEntry(
        name=agent.name,
        description=_first_line(agent.description),
    )


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()
