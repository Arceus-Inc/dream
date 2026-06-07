"""Spec 04 stage 4c — progressive skill disclosure mechanism.

At session start the registry loads only frontmatter (``name``,
``description``, ``when_to_use``) per skill. The body loads on the
agent's explicit ``use_skill(name)`` call, emitting a
``context.skill.loaded`` event, and stays loaded for the session unless
evicted by compaction.

Spec 06 owns skill *authoring* and the validated registry; this module
consumes a discovered catalogue and only enforces the disclosure rules.
The frontmatter parser is intentionally minimal — no YAML dependency,
the three required keys only — because Spec 04 deliberately constrains
the frontmatter shape (Spec 04 #11).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from dream.services.context_log import ContextEvent, ContextSkillLoaded

_REQUIRED_KEYS = ("name", "description", "when_to_use")
_SKILL_FILENAME = "SKILL.md"
EventSink = Callable[[ContextEvent], None]


# --- shape -------------------------------------------------------------------


@dataclass(frozen=True)
class SkillFrontmatter:
    """The session-start cost of one skill: three short strings."""

    name: str
    description: str
    when_to_use: str


@dataclass
class _SkillEntry:
    frontmatter: SkillFrontmatter
    body_path: Path


# --- parser ------------------------------------------------------------------


def parse_skill_frontmatter(text: str) -> tuple[SkillFrontmatter, str]:
    """Parse a SKILL.md into ``(frontmatter, body)``.

    Format: ``---`` line, key/value lines (``key: value``), ``---`` line,
    body. All three required keys must be present and non-empty; missing
    keys raise ``ValueError`` so an invalid skill never silently loads
    with blank fields.
    """
    if not text.startswith("---"):
        raise ValueError("skill frontmatter must start with '---'")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("skill frontmatter must start with '---'")
    try:
        end_idx = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("skill frontmatter missing closing '---'") from exc

    fields: dict[str, str] = {}
    for raw in lines[1:end_idx]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            raise ValueError(f"malformed frontmatter line: {raw!r}")
        key, _, value = raw.partition(":")
        fields[key.strip()] = value.strip()

    missing = [k for k in _REQUIRED_KEYS if not fields.get(k)]
    if missing:
        raise ValueError(f"skill frontmatter missing required keys: {missing}")

    body = "\n".join(lines[end_idx + 1 :])
    fm = SkillFrontmatter(
        name=fields["name"],
        description=fields["description"],
        when_to_use=fields["when_to_use"],
    )
    return fm, body


# --- discovery ---------------------------------------------------------------


def read_skill_frontmatter(path: Path) -> SkillFrontmatter:
    """Parse only the frontmatter of a SKILL.md, stopping at the closing ``---``.

    Progressive disclosure (Spec 04 #10/#11): startup cost MUST NOT scale with
    body size, so we read line-by-line and stop once the closing fence is seen
    rather than slurping and parsing the whole file body.
    """
    header_lines: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        first = fh.readline()
        if first.strip() != "---":
            raise ValueError("skill frontmatter must start with '---'")
        header_lines.append(first.rstrip("\n"))
        closed = False
        for line in fh:
            header_lines.append(line.rstrip("\n"))
            if line.strip() == "---":
                closed = True
                break
        if not closed:
            raise ValueError("skill frontmatter missing closing '---'")
    fm, _ = parse_skill_frontmatter("\n".join(header_lines))
    return fm


def discover_skill_frontmatter(
    roots: Iterable[Path],
) -> list[SkillFrontmatter]:
    """Walk ``roots`` for ``*/SKILL.md`` files and return only frontmatter."""
    found: list[SkillFrontmatter] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for skill_file in sorted(root.glob(f"*/{_SKILL_FILENAME}")):
            found.append(read_skill_frontmatter(skill_file))
    return found


def _discover_entries(roots: Iterable[Path]) -> dict[str, _SkillEntry]:
    """Internal: keep body paths so :meth:`SkillRegistry.use_skill` can lazy-load."""
    entries: dict[str, _SkillEntry] = {}
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for skill_file in sorted(root.glob(f"*/{_SKILL_FILENAME}")):
            fm = read_skill_frontmatter(skill_file)
            entries[fm.name] = _SkillEntry(frontmatter=fm, body_path=skill_file)
    return entries


# --- registry ----------------------------------------------------------------


@dataclass
class SkillRegistry:
    """Per-session skill state: frontmatter eager, bodies lazy.

    ``loaded_bodies`` caches a skill body after the first ``use_skill``
    so repeated calls don't re-read disk and don't re-emit the load event.
    """

    _entries: dict[str, _SkillEntry] = field(default_factory=dict)
    _loaded_bodies: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dirs(cls, roots: Iterable[Path]) -> SkillRegistry:
        return cls(_entries=_discover_entries(roots))

    def list_frontmatter(self) -> list[SkillFrontmatter]:
        # Stable order so the assembled prompt prefix stays byte-stable.
        return [self._entries[name].frontmatter for name in sorted(self._entries)]

    def loaded_skills(self) -> set[str]:
        return set(self._loaded_bodies)

    def use_skill(
        self,
        name: str,
        *,
        event_sink: EventSink | None = None,
    ) -> str:
        """Return the skill body, loading from disk on first call."""
        if name not in self._entries:
            raise KeyError(f"unknown skill: {name}")
        if name in self._loaded_bodies:
            return self._loaded_bodies[name]

        entry = self._entries[name]
        text = entry.body_path.read_text(encoding="utf-8")
        _, body = parse_skill_frontmatter(text)
        self._loaded_bodies[name] = body
        if event_sink is not None:
            event_sink(ContextSkillLoaded(skill_name=name))
        return body


__all__: list[str] = [
    "SkillFrontmatter",
    "SkillRegistry",
    "discover_skill_frontmatter",
    "parse_skill_frontmatter",
    "read_skill_frontmatter",
]
