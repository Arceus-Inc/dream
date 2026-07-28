"""Governance standing-order extraction (Spec 13F).

Deterministically extracts the constitution's ALWAYS / NEVER lists from
``docs/design-docs/core-beliefs.md`` — the "Standing orders" and "What we don't
do" sections — and renders them as a system-prompt block injected at every
session start (AC #21-22). No paraphrase: bullets are carried verbatim. A
missing file or section warns (as data) but never blocks; the lists default
empty.

When the worktree has no ``core-beliefs.md``, Dream falls back to the
packaged constitution shipped with this repo (workforce standing orders live
there — formerly the Base Prompt).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_STANDING_ORDERS = "standing orders"
_WHAT_WE_DONT_DO = "what we don't do"

# dream/src/dream/services/core_beliefs.py → repo root is parents[3]
_PACKAGED_CORE_BELIEFS = (
    Path(__file__).resolve().parents[3] / "docs" / "design-docs" / "core-beliefs.md"
)


@dataclass(frozen=True)
class StandingOrders:
    """The constitution's ALWAYS / NEVER lists, plus any extraction warnings."""

    always: tuple[str, ...] = ()
    never: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def packaged_core_beliefs_path() -> Path:
    """Path to Dream's shipped ``docs/design-docs/core-beliefs.md``."""
    return _PACKAGED_CORE_BELIEFS


def resolve_core_beliefs_path(repo: Path) -> Path:
    """Prefer the worktree constitution; else Dream's packaged file."""
    candidate = Path(repo) / "docs" / "design-docs" / "core-beliefs.md"
    if candidate.is_file():
        return candidate
    return packaged_core_beliefs_path()


def extract_standing_orders(path: Path) -> StandingOrders:
    """Parse ``core-beliefs.md`` into ALWAYS / NEVER bullet lists (verbatim)."""
    try:
        # errors="replace": a core-beliefs.md with stray non-UTF-8 bytes must not
        # crash session start — decode leniently and extract what's parseable.
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return StandingOrders(warnings=("core-beliefs.md not found or unreadable",))

    always: list[str] = []
    never: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading is not None:
            name = heading.group(1).strip().lower()
            current = always if name == _STANDING_ORDERS else never if name == _WHAT_WE_DONT_DO else None
            continue
        bullet = _BULLET.match(line)
        if bullet is not None and current is not None:
            current.append(bullet.group(1).strip())

    warnings: list[str] = []
    if not always:
        warnings.append("no 'Standing orders' section in core-beliefs.md")
    if not never:
        warnings.append("no 'What we don't do' section in core-beliefs.md")
    return StandingOrders(always=tuple(always), never=tuple(never), warnings=tuple(warnings))


def render_standing_orders(orders: StandingOrders) -> str:
    """Render the ALWAYS / NEVER lists as a system-prompt block (empty if none)."""
    if not orders.always and not orders.never:
        return ""
    lines = ["# Standing orders (from core-beliefs.md — non-negotiable)"]
    if orders.always:
        lines.append("ALWAYS:")
        lines.extend(f"- {item}" for item in orders.always)
    if orders.never:
        lines.append("NEVER:")
        lines.extend(f"- {item}" for item in orders.never)
    return "\n".join(lines)


__all__ = [
    "StandingOrders",
    "extract_standing_orders",
    "packaged_core_beliefs_path",
    "render_standing_orders",
    "resolve_core_beliefs_path",
]
