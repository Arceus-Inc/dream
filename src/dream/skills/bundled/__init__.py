"""Markdown skills shipped with the SDK.

Bundled playbooks live under ``content/<slug>/SKILL.md`` and are discovered the
same way as any other source. The SDK ships none in this slice — the mechanism
is wired so authored playbooks can be dropped in later.
"""

from __future__ import annotations

from pathlib import Path

from dream.skills._loader import discover_skill_metas
from dream.skills._types import SkillMeta

_CONTENT_DIR = Path(__file__).parent / "content"


def get_bundled_metas() -> list[SkillMeta]:
    """Return frontmatter for every bundled skill under ``content/``."""
    return discover_skill_metas([_CONTENT_DIR], source="bundled")


__all__ = ["get_bundled_metas"]
