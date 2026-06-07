"""Test-only helpers for writing SKILL.md fixtures (underscore = pytest skips)."""

from __future__ import annotations

from pathlib import Path


def write_skill(
    root: Path,
    slug: str,
    *,
    name: str | None = None,
    description: str = "A test skill.",
    when_to_use: str = "When testing.",
    body: str = "playbook body line one\nplaybook body line two",
    extra_frontmatter: str = "",
    raw: str | None = None,
) -> Path:
    """Create ``<root>/<slug>/SKILL.md`` and return its path.

    ``raw`` writes the file verbatim (for malformed-frontmatter tests); otherwise
    a valid YAML frontmatter block is assembled from the keyword fields.
    """
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return path
    lines = [
        "---",
        f"name: {name or slug}",
        f"description: {description}",
        f"when_to_use: {when_to_use}",
    ]
    if extra_frontmatter:
        lines.append(extra_frontmatter.rstrip("\n"))
    lines += ["---", body, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
