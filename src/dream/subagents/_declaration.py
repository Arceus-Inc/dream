"""Role-agnostic subagent declarations.

A ``Subagent`` is a frozen capability-minimized template. The live path is
``spawn_subagent`` → ``run_subagent_delegate`` → ``run_subagent_session`` →
``run_role``. The declaring application owns role policy; Dream executes the
typed shape.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from dream.api.response_format import JsonSchema
from dream.subagents._isolation import IsolationMode
from dream.subagents._overlay import PermissionOverlay

PermissionDelta = PermissionOverlay
"""Tighten-only permission overlay. Never widens the parent."""

MAX_INLINE_NESTING = 2
"""Hard cap on mid-beat subagent nesting (Hermes-flat default; depth-2 for rare orchestrators).

A subagent at ``depth < MAX_INLINE_NESTING`` may dispatch its declared ``spawnable``
children; at the cap it is always a leaf.
"""

# Back-compat alias — prefer ``MAX_INLINE_NESTING``.
MAX_SUBAGENT_DEPTH = MAX_INLINE_NESTING

GENERAL_PURPOSE_NAME = "generalPurpose"
"""Built-in ad-hoc worker type always offered when spawn is enabled."""

GENERAL_PURPOSE_DESCRIPTION = (
    "Ad-hoc delegated worker. Fresh context; returns a summary. "
    "Use for reasoning-heavy subtasks that would flood the parent."
)


@dataclass(frozen=True)
class Subagent:
    """Harness-side subagent declaration (Tier-1 role or Tier-2 registry)."""

    name: str
    description: str
    """One-line discovery copy for :class:`SubagentCatalogue`."""

    tools: tuple[str, ...]
    skills: tuple[str, ...] = ()
    permission_overlay: PermissionOverlay = field(default_factory=PermissionOverlay)
    depth: int = 1
    model: str | None = None
    spawned_by: tuple[str, ...] = ()
    system_prompt: str | None = None
    max_turns: int = 8
    spawnable: tuple[Subagent, ...] = ()
    output_schema: JsonSchema | Mapping[str, object] | None = None
    strict: bool = False
    isolation: IsolationMode = IsolationMode.SHARED
    """``SHARED`` = parent worktree; ``WORKTREE`` = short-lived git worktree."""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Subagent.name must be a non-empty string")
        if not self.description:
            raise ValueError("Subagent.description must be a non-empty string")
        if isinstance(self.tools, str):
            raise TypeError("Subagent.tools must be a sequence of strings, not a bare string")
        if self.depth < 1:
            raise ValueError(f"Subagent.depth must be >= 1; got {self.depth}")
        if not isinstance(self.isolation, IsolationMode):
            raise TypeError(f"Subagent.isolation must be IsolationMode; got {type(self.isolation)}")
        object.__setattr__(
            self, "permission_overlay", PermissionOverlay.parse(self.permission_overlay)
        )

    def to_dict(self) -> dict[str, object]:
        schema_doc: dict[str, object] | None
        if self.output_schema is None:
            schema_doc = None
        elif isinstance(self.output_schema, JsonSchema):
            schema_doc = dict(self.output_schema.document)
        else:
            schema_doc = dict(self.output_schema)
        return {
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "skills": list(self.skills),
            "permission_overlay": list(self.permission_overlay.as_tokens()),
            "depth": self.depth,
            "model": self.model,
            "spawned_by": list(self.spawned_by),
            "system_prompt": self.system_prompt,
            "max_turns": self.max_turns,
            "output_schema": schema_doc,
            "strict": self.strict,
            "isolation": self.isolation.value,
            "spawnable": [child.to_dict() for child in self.spawnable],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Subagent:
        raw_schema = data.get("output_schema")
        output_schema: JsonSchema | None
        if raw_schema is None:
            output_schema = None
        elif isinstance(raw_schema, JsonSchema):
            output_schema = raw_schema
        elif isinstance(raw_schema, Mapping):
            output_schema = JsonSchema.of(cast(Mapping[str, object], raw_schema))
        else:
            raise TypeError(
                f"Subagent.output_schema must be a mapping or JsonSchema; got {type(raw_schema)}"
            )

        tools_raw = data["tools"]
        if not isinstance(tools_raw, Sequence) or isinstance(tools_raw, (str, bytes)):
            raise TypeError("Subagent.tools must be a sequence of strings")

        spawnable_raw = data.get("spawnable", ())
        if spawnable_raw is None:
            spawnable_raw = ()
        if not isinstance(spawnable_raw, Sequence) or isinstance(spawnable_raw, (str, bytes)):
            raise TypeError("Subagent.spawnable must be a sequence")

        isolation_raw = data.get("isolation", IsolationMode.SHARED.value)
        isolation = (
            isolation_raw
            if isinstance(isolation_raw, IsolationMode)
            else IsolationMode(str(isolation_raw))
        )

        return cls(
            name=str(data["name"]),
            description=str(data["description"]),
            tools=tuple(str(item) for item in tools_raw),
            skills=_string_tuple(data, "skills"),
            permission_overlay=PermissionOverlay.parse(_string_tuple(data, "permission_overlay")),
            depth=_int_field(data, "depth", 1),
            model=str(data["model"]) if data.get("model") is not None else None,
            spawned_by=_string_tuple(data, "spawned_by"),
            system_prompt=(
                str(data["system_prompt"]) if data.get("system_prompt") is not None else None
            ),
            max_turns=_int_field(data, "max_turns", 8),
            output_schema=output_schema,
            strict=bool(data["strict"]) if "strict" in data else False,
            isolation=isolation,
            spawnable=tuple(
                cls.from_dict(cast(Mapping[str, object], child)) for child in spawnable_raw
            ),
        )


def _int_field(data: Mapping[str, object], key: str, default: int) -> int:
    if key not in data:
        return default
    return int(str(data[key]))


def _string_tuple(data: Mapping[str, object], key: str) -> tuple[str, ...]:
    if key not in data or data[key] is None:
        return ()
    raw = data[key]
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise TypeError(f"Subagent.{key} must be a sequence of strings")
    return tuple(str(item) for item in raw)


@dataclass(frozen=True)
class SubagentSet:
    """Resolved set of subagents available to one beat."""

    agents: dict[str, Subagent] = field(default_factory=dict)

    def get(self, name: str) -> Subagent | None:
        return self.agents.get(name)

    def names(self) -> list[str]:
        return list(self.agents.keys())

    def descriptions(self) -> dict[str, str]:
        return {name: agent.description for name, agent in self.agents.items()}

    def __iter__(self) -> Iterator[Subagent]:
        return iter(self.agents.values())

    def __contains__(self, name: str) -> bool:
        return name in self.agents

    def __len__(self) -> int:
        return len(self.agents)

    def __bool__(self) -> bool:
        return bool(self.agents)


__all__ = [
    "GENERAL_PURPOSE_DESCRIPTION",
    "GENERAL_PURPOSE_NAME",
    "MAX_INLINE_NESTING",
    "MAX_SUBAGENT_DEPTH",
    "PermissionDelta",
    "PermissionOverlay",
    "Subagent",
    "SubagentSet",
]
