"""``SkillRegistry`` — name → skill lookup with lazy bodies (Spec 06).

Frontmatter is eager (the catalogue), bodies are lazy: ``use_skill`` reads the
body from disk on first call, caches it, and emits ``context.skill.loaded``.
Lookup tolerates the command name, aliases, and case variants.
"""

from __future__ import annotations

from collections.abc import Callable

from dream.services.context_log import ContextEvent, ContextSkillLoaded
from dream.skills._frontmatter import read_skill_body
from dream.skills._types import SkillDefinition, SkillMeta, SkillShadow

EventSink = Callable[[ContextEvent], None]


class SkillRegistry:
    """Per-session skill catalogue: frontmatter eager, bodies lazy."""

    def __init__(self) -> None:
        self._by_name: dict[str, SkillMeta] = {}  # lowercased canonical name -> meta
        self._lookup: dict[str, str] = {}  # lowercased key -> lowercased canonical name
        self._bodies: dict[str, str] = {}

    def register(self, meta: SkillMeta) -> SkillShadow | None:
        """Register ``meta``; return a :class:`SkillShadow` if it replaced a name.

        Collisions are detected on the *normalized* (lowercased) name so two
        names differing only by case shadow each other — matching the
        case-insensitive resolution below. Re-registering a canonical name first
        clears the shadowed definition's lookup keys so stale aliases/command
        names never keep resolving to the replacement.
        """
        canonical = meta.name.lower()
        existing = self._by_name.get(canonical)
        shadow: SkillShadow | None = None
        if existing is not None:
            shadow = SkillShadow(
                name=meta.name,
                winner_source=meta.source,
                shadowed_source=existing.source,
                winner_path=meta.path,
                shadowed_path=existing.path,
            )
            self._drop_lookup_keys(canonical)
        self._by_name[canonical] = meta
        for key in self._lookup_keys(meta):
            self._lookup[key] = canonical
        return shadow

    def resolve(self, name: str) -> SkillMeta | None:
        """Look up a skill by name/command/alias, tolerating case variants."""
        canonical = self._lookup.get(name.lower())
        return self._by_name.get(canonical) if canonical is not None else None

    def list_meta(self) -> list[SkillMeta]:
        """Return every registered skill's metadata, name-sorted (byte-stable)."""
        return [self._by_name[name] for name in sorted(self._by_name)]

    def loaded_skills(self) -> set[str]:
        """Names whose bodies are currently loaded in this session."""
        return set(self._bodies)

    def use_skill(self, name: str, *, event_sink: EventSink | None = None) -> SkillDefinition:
        """Materialise a skill (load + cache body, emit the loaded event)."""
        meta = self.resolve(name)
        if meta is None:
            raise KeyError(f"unknown skill: {name}")
        if meta.name in self._bodies:
            return SkillDefinition(meta=meta, content=self._bodies[meta.name])
        if meta.path is None:
            raise KeyError(f"skill {meta.name!r} has no body to load")
        body = read_skill_body(meta.path)
        self._bodies[meta.name] = body
        if event_sink is not None:
            event_sink(ContextSkillLoaded(skill_name=meta.name))
        return SkillDefinition(meta=meta, content=body)

    def _drop_lookup_keys(self, canonical: str) -> None:
        """Remove every lookup key currently pointing at ``canonical``."""
        stale = [key for key, target in self._lookup.items() if target == canonical]
        for key in stale:
            del self._lookup[key]

    @staticmethod
    def _lookup_keys(meta: SkillMeta) -> set[str]:
        keys = {meta.name}
        if meta.command_name:
            keys.add(meta.command_name)
        if meta.display_name:
            keys.add(meta.display_name)
        keys.update(meta.aliases)
        return {key.lower() for key in keys}


__all__ = ["EventSink", "SkillRegistry"]
