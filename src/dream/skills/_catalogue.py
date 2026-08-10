"""Render the skill frontmatter catalogue for the model's prompt (Spec 06).

Only the three disclosure fields (``name``/``description``/``when_to_use``) of
*model-invocable* skills go into the prompt — user-only skills
(``disable_model_invocation``) are excluded since the model can't load them.
"""

from __future__ import annotations

from collections.abc import Sequence

from dream.skills._types import SkillMeta

_HEADER = "# Skills"
_INTRO = "Load a skill's playbook with the `skill` tool when its guidance applies:"


def render_skill_catalogue(metas: Sequence[SkillMeta]) -> str:
    """Return a compact catalogue block, or ``""`` when no model skills exist."""
    model_skills = sorted(
        (m for m in metas if not m.disable_model_invocation), key=lambda m: m.name
    )
    if not model_skills:
        return ""
    lines = [_HEADER, "", _INTRO]
    lines += [
        f"- **{m.name}** — {m.description} (when: {m.when_to_use})" for m in model_skills
    ]
    return "\n".join(lines)


__all__ = ["render_skill_catalogue"]
