"""Role-agnostic subagent declarations.

A ``Subagent`` is a thin overlay declaration projected onto Dream's existing
``TeammateSpawnConfig`` at beat-build time. The declaration is durable (lives on
the role / in the registry); the spawn config is ephemeral (minted per dispatch).

The declaring application owns role policy; Dream only executes the typed shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from dream.api.response_format import JsonSchema

PermissionDelta = tuple[str, ...]
"""Tighten-only permission overlay — a tuple of permission tokens to *remove*
from the parent's set. Never widens."""

MAX_SUBAGENT_DEPTH = 2
"""Hard cap on subagent nesting. A subagent at ``depth < MAX_SUBAGENT_DEPTH`` may dispatch its
declared ``spawnable`` children; at the cap it is always a leaf. V1 was flat (1); depth-2 lets a
Tier-1 specialist spawn a Tier-2 orchestrator, bounded by construction."""


@dataclass(frozen=True)
class Subagent:
    """Harness-side subagent declaration.

    Declared on the role (Tier-1) or in the shared SubagentRegistry (Tier-2).
    Projected onto dream's TeammateSpawnConfig at dispatch time.
    """

    name: str
    """Sanitized identifier — 'reviewer', 'query_orchestrator', etc."""

    description: str
    """What it's for (used in the spawn tool's schema for model discovery)."""

    tools: tuple[str, ...]
    """Capability-minimized allow-list — must be a subset of the parent's tools."""

    skills: tuple[str, ...] = ()
    """Authored know-how the subagent consults (skill names)."""

    permission_overlay: PermissionDelta = ()
    """Tighten-only (never widen) — permissions to *drop* from the parent."""

    depth: int = 1
    """Dream depth slot; must be > parent.depth. V1 is flat: always 1."""

    model: str | None = None
    """Optional cheaper model for the subagent. None → parent model."""

    spawned_by: tuple[str, ...] = ()
    """Which parents may dispatch it (Tier-2 gating). Empty → any parent."""

    system_prompt: str | None = None
    """Optional custom system prompt. None → generated from name+description."""

    max_turns: int = 8
    """Maximum turn budget for the subagent before forced termination."""

    spawnable: tuple[Subagent, ...] = ()
    """The Tier-2 subagents THIS subagent may itself dispatch (depth-2). Empty (default) = a leaf,
    unchanged from v1. Non-empty + ``depth < MAX_SUBAGENT_DEPTH`` makes the child spawn-eligible: it
    keeps ``spawn_subagent`` and is handed a scoped set of exactly these agents — never the parent's
    full roster. Each is still tool-intersected with the child, so a grandchild can only narrow."""

    output_schema: JsonSchema | Mapping[str, object] | None = None
    """Optional JSON-schema the subagent's final message is validated against at runtime. ``None`` =
    no enforcement (free-text return, unchanged). When set, the inline executor coerces + validates the
    output, runs a bounded reformat loop on failure, and fails open with a warning (``_output_guard``)
    unless ``strict`` is True."""

    strict: bool = False
    """When True with ``output_schema``, exhausted repairs raise
    :class:`~dream.subagents._output_guard.OutputSchemaError` instead of fail-open.
    Use for DoD graders (api_verifier, test_author)."""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Subagent.name must be a non-empty string")
        if not self.description:
            raise ValueError("Subagent.description must be a non-empty string")
        if isinstance(self.tools, str):
            raise TypeError("Subagent.tools must be a sequence of strings, not a bare string")
        if self.depth < 1:
            raise ValueError(f"Subagent.depth must be >= 1; got {self.depth}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "skills": list(self.skills),
            "permission_overlay": list(self.permission_overlay),
            "depth": self.depth,
            "model": self.model,
            "spawned_by": list(self.spawned_by),
            "system_prompt": self.system_prompt,
            "max_turns": self.max_turns,
            "output_schema": (
                dict(self.output_schema.document)
                if isinstance(self.output_schema, JsonSchema)
                else (dict(self.output_schema) if self.output_schema is not None else None)
            ),
            "strict": self.strict,
            "spawnable": [child.to_dict() for child in self.spawnable],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Subagent:
        raw_schema = data.get("output_schema")
        output_schema: JsonSchema | None
        if raw_schema is None:
            output_schema = None
        elif isinstance(raw_schema, JsonSchema):
            output_schema = raw_schema
        elif isinstance(raw_schema, Mapping):
            output_schema = JsonSchema.of(raw_schema)
        else:
            raise TypeError(
                f"Subagent.output_schema must be a mapping or JsonSchema; got {type(raw_schema)}"
            )
        return cls(
            name=data["name"],
            description=data["description"],
            tools=tuple(data["tools"]),
            skills=tuple(data.get("skills") or ()),
            permission_overlay=tuple(data.get("permission_overlay") or ()),
            depth=data.get("depth", 1),
            model=data.get("model"),
            spawned_by=tuple(data.get("spawned_by") or ()),
            system_prompt=data.get("system_prompt"),
            max_turns=data.get("max_turns", 8),
            output_schema=output_schema,
            strict=bool(data.get("strict", False)),
            spawnable=tuple(cls.from_dict(child) for child in (data.get("spawnable") or ())),
        )


@dataclass(frozen=True)
class SubagentSet:
    """The resolved set of subagents available to one beat.

    Built by the harness factory: merges Tier-1 (role-owned) and Tier-2
    (shared registry) subagents, intersects each with the parent's live
    toolset/permissions, and freezes the result.
    """

    agents: dict[str, Subagent] = field(default_factory=dict)
    """name → Subagent mapping. Immutable after construction."""

    def get(self, name: str) -> Subagent | None:
        return self.agents.get(name)

    def names(self) -> list[str]:
        return list(self.agents.keys())

    def descriptions(self) -> dict[str, str]:
        """Return {name: description} for tool-schema generation."""
        return {name: sa.description for name, sa in self.agents.items()}

    def __contains__(self, name: str) -> bool:
        return name in self.agents

    def __len__(self) -> int:
        return len(self.agents)

    def __bool__(self) -> bool:
        return bool(self.agents)
