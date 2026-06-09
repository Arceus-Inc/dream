"""Session-start skill validation (Spec 06 MUST #3, fail fast).

Malformed frontmatter must block the session. The slice-1 parser already
raises ``SkillFrontmatterError``; this walks the same source directories and
turns each parse failure into a *blocking* ``Finding`` so the session-start
gate (``has_blocking``) can refuse to start instead of crashing.
"""

from __future__ import annotations

from pathlib import Path

from dream.services.repo_validator import Finding
from dream.skills._frontmatter import SkillFrontmatterError, read_skill_meta
from dream.skills._session import session_skill_dirs

_SKILL_FILENAME = "SKILL.md"


def validate_skills(
    working_dir: Path,
    *,
    home: Path | None = None,
    allow_project_skills: bool = True,
) -> list[Finding]:
    """Return a blocking ``Finding`` for every malformed SKILL.md under the sources."""
    user_dirs, project_dirs = session_skill_dirs(working_dir, home=home)
    roots = [*user_dirs, *project_dirs] if allow_project_skills else list(user_dirs)

    findings: list[Finding] = []
    for root in roots:
        if not root.is_dir():
            continue
        for skill_file in sorted(root.glob(f"*/{_SKILL_FILENAME}")):
            try:
                # ``source`` is irrelevant for validation; we only care that it parses.
                read_skill_meta(skill_file, source="project")
            except SkillFrontmatterError as exc:
                findings.append(_blocking(skill_file, f"malformed skill frontmatter: {exc}"))
            except (OSError, UnicodeDecodeError) as exc:
                # Unreadable file or non-UTF-8 content: fail closed as a blocking
                # finding rather than crashing session startup.
                findings.append(_blocking(skill_file, f"unreadable skill file: {exc}"))
    return findings


def _blocking(skill_file: Path, message: str) -> Finding:
    return Finding(
        severity="blocking",
        code="skill_frontmatter_invalid",
        message=message,
        path=str(skill_file),
    )


__all__ = ["validate_skills"]
