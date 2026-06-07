"""Skill data shapes (Spec 06).

Two shapes by design:

- :class:`SkillMeta` is the *catalogue* entry — everything from the frontmatter
  plus where the skill came from. It is what sits in the registry (and, for the
  three preamble fields, in the stable prompt) at session start. It carries no
  body, so the session-start cost never scales with body size (progressive
  disclosure, Spec 04 #10/#11).
- :class:`SkillDefinition` is the *materialised* shape — a ``SkillMeta`` plus the
  body ``content`` — produced only when the agent explicitly loads a skill via
  ``SkillRegistry.use_skill``.

:class:`SkillShadow` records a name collision across sources so the session-start
(Slice 2) can surface it as a warning rather than overwriting silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SkillSource = Literal["bundled", "user", "project", "plugin"]


@dataclass(frozen=True)
class SkillMeta:
    """One skill's catalogue entry: validated frontmatter + provenance."""

    name: str
    description: str
    when_to_use: str
    source: SkillSource
    path: Path | None = None
    base_dir: Path | None = None
    command_name: str | None = None
    display_name: str | None = None
    aliases: tuple[str, ...] = ()
    tools_required: tuple[str, ...] = ()
    risk: str = "safe"
    user_invocable: bool = True
    disable_model_invocation: bool = False
    model: str | None = None
    argument_hint: str | None = None


@dataclass(frozen=True)
class SkillDefinition:
    """A skill with its body loaded — produced on ``use_skill``."""

    meta: SkillMeta
    content: str


@dataclass(frozen=True)
class SkillShadow:
    """A later source shadowed an earlier skill of the same name."""

    name: str
    winner_source: SkillSource
    shadowed_source: SkillSource
    winner_path: Path | None = None
    shadowed_path: Path | None = None


__all__ = ["SkillDefinition", "SkillMeta", "SkillShadow", "SkillSource"]
