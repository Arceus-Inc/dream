"""Render the memory catalogue for the model's prompt (spec 11 disclosure).

Memory is progressively disclosed like skills: only a one-line teaser per
record (``id`` + ``description``) goes into the system prompt so the model
knows what facts exist; full bodies load lazily via the ``memory_get`` tool.
``description`` prefers the frontmatter field, falling back to the first
non-empty line of the body, truncated so a long record never bloats the prompt.
"""

from __future__ import annotations

from collections.abc import Sequence

from dream.contracts.memory import MemoryRecord

__all__ = ["memory_description", "render_memory_catalogue"]

_HEADER = "# Workspace memory"
_INTRO = "Durable facts about this workspace. Load a record's full content with the `memory_get` tool (or find more with `memory_search`):"
_DESCRIPTION_MAX = 120


def render_memory_catalogue(records: Sequence[MemoryRecord]) -> str:
    """Return a compact catalogue block, or ``""`` when no records exist."""
    ordered = sorted(records, key=lambda r: r.id)
    if not ordered:
        return ""
    lines = [_HEADER, "", _INTRO]
    lines += [f"- {r.id} — {memory_description(r)}" for r in ordered]
    return "\n".join(lines)


def memory_description(record: MemoryRecord) -> str:
    """One-line teaser for a record: frontmatter ``description`` or body lead."""
    raw = record.frontmatter.get("description")
    text = str(raw).strip() if raw else _first_nonempty_line(record.content)
    if len(text) > _DESCRIPTION_MAX:
        return text[: _DESCRIPTION_MAX - 1].rstrip() + "…"
    return text


def _first_nonempty_line(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""
