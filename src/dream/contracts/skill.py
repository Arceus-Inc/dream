"""Skill: a markdown playbook the agent can load on demand."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Skill:
    """A named playbook composed of markdown content and frontmatter."""

    name: str
    description: str
    content: str
    source: Path | None = None
    allowed_tools: tuple[str, ...] = ()
    frontmatter: dict[str, Any] = field(default_factory=dict)
