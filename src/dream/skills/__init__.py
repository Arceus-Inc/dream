"""Skill loader, registry, and bundled markdown playbooks (Spec 06).

Skills are progressively-disclosed Markdown playbooks: only the frontmatter
(``name``/``description``/``when_to_use``) is eager (the catalogue + the stable
prompt preamble); bodies load lazily on ``use_skill``.
"""

from __future__ import annotations

from dream.skills._frontmatter import (
    SkillFrontmatterError,
    parse_skill_meta,
    read_skill_body,
    read_skill_meta,
)
from dream.skills._loader import discover_skill_metas, load_skill_registry
from dream.skills._registry import SkillRegistry
from dream.skills._types import SkillDefinition, SkillMeta, SkillShadow, SkillSource

__all__ = [
    "SkillDefinition",
    "SkillFrontmatterError",
    "SkillMeta",
    "SkillRegistry",
    "SkillShadow",
    "SkillSource",
    "discover_skill_metas",
    "load_skill_registry",
    "parse_skill_meta",
    "read_skill_body",
    "read_skill_meta",
]
