"""Layered skill loader (Spec 06).

Registers skills from four sources in precedence order — bundled → user →
project → plugin — into one :class:`SkillRegistry`, a later source shadowing an
earlier one by name. Shadows are returned as data (no ``logging`` in ``src``);
the session-start surfaces them as warnings.

The loader takes resolved directories explicitly so it is decoupled from
``DreamPaths``/settings and trivially unit-testable; the call site composes the
user/project directories and the ``allow_project_skills`` setting.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from dream.skills._frontmatter import read_skill_meta
from dream.skills._registry import SkillRegistry
from dream.skills._types import SkillMeta, SkillShadow, SkillSource

_SKILL_FILENAME = "SKILL.md"


def discover_skill_metas(dirs: Iterable[Path], *, source: SkillSource) -> list[SkillMeta]:
    """Read frontmatter for every ``<dir>/*/SKILL.md`` under ``dirs``."""
    metas: list[SkillMeta] = []
    seen: set[Path] = set()
    for directory in dirs:
        root = Path(directory)
        if not root.is_dir():
            continue
        for skill_file in sorted(root.glob(f"*/{_SKILL_FILENAME}")):
            resolved = skill_file.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            metas.append(read_skill_meta(skill_file, source=source))
    return metas


def load_skill_registry(
    *,
    bundled: Iterable[SkillMeta] = (),
    user_dirs: Iterable[Path] = (),
    project_dirs: Iterable[Path] = (),
    plugin_skills: Iterable[SkillMeta] = (),
    allow_project_skills: bool = True,
) -> tuple[SkillRegistry, list[SkillShadow]]:
    """Build a registry from the four layered sources; collect shadow records."""
    registry = SkillRegistry()
    shadows: list[SkillShadow] = []

    def _register_all(metas: Iterable[SkillMeta]) -> None:
        for meta in metas:
            shadow = registry.register(meta)
            if shadow is not None:
                shadows.append(shadow)

    _register_all(bundled)
    _register_all(discover_skill_metas(user_dirs, source="user"))
    if allow_project_skills:
        _register_all(discover_skill_metas(project_dirs, source="project"))
    _register_all(plugin_skills)
    return registry, shadows


__all__ = ["discover_skill_metas", "load_skill_registry"]
