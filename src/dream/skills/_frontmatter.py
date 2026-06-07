"""YAML frontmatter parsing for ``SKILL.md`` files (Spec 06).

Strict by design (Spec 06 #2): a skill whose frontmatter is missing a required
key (``name``/``description``/``when_to_use``) raises ``SkillFrontmatterError``
so the session-start (Slice 2) can refuse to start. This diverges from the
OpenHarness reference, which falls back to a heading + first paragraph.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dream.skills._types import SkillMeta, SkillSource

_FENCE = "---"


class SkillFrontmatterError(ValueError):
    """Raised when a SKILL.md has missing/invalid frontmatter."""


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split a SKILL.md into ``(frontmatter_yaml, body)``.

    Raises :class:`SkillFrontmatterError` if the ``---`` fences are absent.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise SkillFrontmatterError("skill frontmatter must start with '---'")
    try:
        end = lines.index(_FENCE, 1)
    except ValueError as exc:
        raise SkillFrontmatterError("skill frontmatter missing closing '---'") from exc
    header = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    return header, body


def parse_skill_meta(
    frontmatter_yaml: str,
    *,
    source: SkillSource,
    path: Path | None = None,
    base_dir: Path | None = None,
    command_name: str | None = None,
) -> SkillMeta:
    """Parse + validate frontmatter into a :class:`SkillMeta`."""
    try:
        loaded = yaml.safe_load(frontmatter_yaml) if frontmatter_yaml.strip() else {}
    except yaml.YAMLError as exc:
        raise SkillFrontmatterError(f"invalid YAML frontmatter: {exc}") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise SkillFrontmatterError(
            f"frontmatter must be a mapping, got {type(loaded).__name__}"
        )

    name = _required_str(loaded, "name")
    display_name = name if command_name is not None and name != command_name else None
    return SkillMeta(
        name=name,
        description=_required_str(loaded, "description"),
        when_to_use=_required_str(loaded, "when_to_use"),
        source=source,
        path=path,
        base_dir=base_dir,
        command_name=command_name,
        display_name=display_name,
        aliases=_str_tuple(loaded.get("aliases")),
        tools_required=_str_tuple(loaded.get("tools_required")),
        risk=_optional_str(loaded.get("risk")) or "safe",
        user_invocable=_permissive_bool(loaded.get("user_invocable"), default=True),
        disable_model_invocation=_permissive_bool(
            loaded.get("disable_model_invocation"), default=False
        ),
        model=_optional_str(loaded.get("model")),
        argument_hint=_optional_str(loaded.get("argument_hint")),
    )


def read_skill_meta(path: Path, *, source: SkillSource) -> SkillMeta:
    """Read only the frontmatter of a SKILL.md (no body) into a :class:`SkillMeta`.

    Reads line-by-line and stops at the closing fence so startup cost never
    scales with body size (Spec 04 #10/#11). ``command_name`` defaults to the
    skill's directory name.
    """
    header = _read_header_only(path)
    return parse_skill_meta(
        header,
        source=source,
        path=path,
        base_dir=path.parent,
        command_name=path.parent.name,
    )


def read_skill_body(path: Path) -> str:
    """Read a SKILL.md and return only its body (frontmatter stripped)."""
    _, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return body


# --- internals ---------------------------------------------------------------


def _read_header_only(path: Path) -> str:
    header_lines: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        first = fh.readline()
        if first.strip() != _FENCE:
            raise SkillFrontmatterError("skill frontmatter must start with '---'")
        closed = False
        for line in fh:
            if line.strip() == _FENCE:
                closed = True
                break
            header_lines.append(line.rstrip("\n"))
        if not closed:
            raise SkillFrontmatterError("skill frontmatter missing closing '---'")
    return "\n".join(header_lines)


def _required_str(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillFrontmatterError(f"frontmatter missing required key: {key!r}")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(p.strip() for p in value.split(",") if p.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise SkillFrontmatterError(f"expected a list of strings, got {type(value).__name__}")


def _permissive_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


__all__ = [
    "SkillFrontmatterError",
    "parse_skill_meta",
    "read_skill_body",
    "read_skill_meta",
    "split_frontmatter",
]
