"""Per-session skill wiring (Spec 06, Slice 2).

Carries the session's :class:`SkillRegistry` (plus the set of available tool
names and an optional context-event sink) to the ``skill`` tool through the
generic ``ToolExecutionContext.metadata`` channel, so the engine stays
skill-agnostic and the tool reads a typed bundle rather than poking ``Any``.

Also composes the session registry from the four sources at the call site that
slice 1 deliberately left to the caller (bundled + user + project dirs).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dream.config.paths import DreamPaths
from dream.skills._loader import load_skill_registry
from dream.skills._registry import EventSink, SkillRegistry
from dream.skills._types import SkillShadow
from dream.skills.bundled import get_bundled_metas

SKILL_CONTEXT_KEY = "skill_context"

# Project-local skill roots, relative to the working dir. ``docs/skills`` is the
# conceptual home (Spec 06 #1); the others import existing skill libraries.
_PROJECT_SKILL_DIRS = ("docs/skills", ".agents/skills", ".claude/skills")


@dataclass(frozen=True)
class SkillContext:
    """The per-session skill state a ``skill`` tool call needs."""

    registry: SkillRegistry
    available_tools: frozenset[str]
    event_sink: EventSink | None = None


def put_skill_context(metadata: dict[str, object], skill_context: SkillContext) -> None:
    """Place ``skill_context`` into a tool ``metadata`` dict under the known key."""
    metadata[SKILL_CONTEXT_KEY] = skill_context


def read_skill_context(metadata: dict[str, object]) -> SkillContext | None:
    """Return the :class:`SkillContext` from tool ``metadata``, or ``None``."""
    value = metadata.get(SKILL_CONTEXT_KEY)
    return value if isinstance(value, SkillContext) else None


def session_skill_dirs(
    working_dir: Path, *, home: Path | None = None
) -> tuple[list[Path], list[Path]]:
    """Return ``(user_dirs, project_dirs)`` for a session's skill sources."""
    paths = DreamPaths.resolve(Path(working_dir), home=home)
    user_dirs = [paths.skills_dir]
    project_dirs = [Path(working_dir) / rel for rel in _PROJECT_SKILL_DIRS]
    return user_dirs, project_dirs


def build_session_skill_registry(
    working_dir: Path,
    *,
    home: Path | None = None,
    allow_project_skills: bool = True,
) -> tuple[SkillRegistry, list[SkillShadow]]:
    """Compose bundled + user + project skills into one registry for a session."""
    user_dirs, project_dirs = session_skill_dirs(working_dir, home=home)
    return load_skill_registry(
        bundled=get_bundled_metas(),
        user_dirs=user_dirs,
        project_dirs=project_dirs,
        allow_project_skills=allow_project_skills,
    )


__all__ = [
    "SKILL_CONTEXT_KEY",
    "SkillContext",
    "build_session_skill_registry",
    "put_skill_context",
    "read_skill_context",
    "session_skill_dirs",
]
