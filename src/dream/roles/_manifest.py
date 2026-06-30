"""``RoleManifest`` pydantic value object (Spec 10 §Artefact shapes).

The shape is fixed by the spec: name is one of three canonical strings,
``tools=null`` is reserved to the generator (meaning "all, intersected with
the active sandbox tier"), and ``permission_mode`` deliberately omits
``bypassPermissions`` — there is no v1 role that may bypass the permission
gate.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

RoleName = Literal["planner", "generator", "evaluator"]
SystemPromptMode = Literal["default", "replace", "append"]
PermissionMode = Literal["default", "acceptEdits", "plan", "dontAsk"]
Isolation = Literal["worktree", "remote"]
MemoryScope = Literal["user", "project", "local"]
Effort = Literal["low", "medium", "high"]


class RoleManifest(BaseModel):
    """One role's standing contract.

    Tuples (not lists) on the collection fields so the model is hashable and
    safe to share across threads / async tasks; pydantic coerces input lists.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: RoleName
    description: str
    system_prompt: str
    system_prompt_mode: SystemPromptMode = "default"
    tools: tuple[str, ...] | None = None
    disallowed_tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    permission_mode: PermissionMode = "default"
    isolation: Isolation = "worktree"
    memory_scope: MemoryScope = "project"
    effort: Effort = "medium"
    color: str = "neutral"
    subagents: tuple[str, ...] = ()
    """Tier-1 role-owned subagent names declared on this role.
    Resolved from the SubagentRegistry at beat-build time."""
    shared_subagents: tuple[str, ...] = ()
    """Tier-2 shared subagent names this role may dispatch (from SubagentRegistry)."""

    @model_validator(mode="after")
    def _only_generator_may_use_null_tools(self) -> RoleManifest:
        if self.tools is None and self.name != "generator":
            raise ValueError(
                f"tools=null is reserved for the generator role; "
                f"role {self.name!r} must declare an explicit allow-list"
            )
        return self
