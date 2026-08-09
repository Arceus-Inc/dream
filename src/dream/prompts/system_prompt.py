"""Assemble the session system prompt from stable, context, and role blocks."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files


def packaged_standing_orders() -> str:
    """Return Dream's installed standing orders, never a workspace document."""
    return files("dream.prompts").joinpath("standing_orders.md").read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class StablePromptBlock:
    """Prompt content that is identical across equivalent session turns."""

    def render(self) -> str:
        return _render_block("stable", packaged_standing_orders())


@dataclass(frozen=True)
class ContextPromptBlock:
    """Workspace catalogues that ground a session without defining its role."""

    workspace_governance: str
    skill_catalogue: str
    memory_catalogue: str

    def render(self) -> str:
        content = _join(self.workspace_governance, self.skill_catalogue, self.memory_catalogue)
        return _render_block("context", content) if content else ""


@dataclass(frozen=True)
class RolePromptBlock:
    """Caller-supplied role or task framing for the session."""

    instructions: str | None

    def render(self) -> str:
        content = _join(self.instructions)
        return _render_block("role", content) if content else ""


@dataclass(frozen=True)
class RuntimeContextBlock:
    """Volatile host facts injected before the first user turn."""

    runtime_info: str

    def render(self) -> str:
        return _render_block("runtime-context", self.runtime_info)


def assemble_session_system_prompt(
    *,
    stable: StablePromptBlock,
    context: ContextPromptBlock,
    role: RolePromptBlock,
) -> str:
    """Render the deterministic stable-first system-prompt sequence."""
    return "\n\n".join(block for block in (stable.render(), context.render(), role.render()) if block)


def _join(*parts: str | None) -> str:
    return "\n\n".join(part for part in parts if part)


def _render_block(name: str, content: str) -> str:
    return f"<{name}>\n{content}\n</{name}>"


__all__ = [
    "ContextPromptBlock",
    "RolePromptBlock",
    "RuntimeContextBlock",
    "StablePromptBlock",
    "assemble_session_system_prompt",
    "packaged_standing_orders",
]
