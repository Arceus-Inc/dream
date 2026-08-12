"""Assemble the session system prompt from stable, context, and role blocks."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

_STANDING_ORDERS = files("dream.prompts").joinpath("standing_orders")
_PHASE_CHAPTERS = frozenset({"planner", "generator", "evaluator"})


def packaged_standing_orders(*, role: str | None = None) -> str:
    """Return Dream's packaged standing orders (common + optional phase chapter)."""
    common = _STANDING_ORDERS.joinpath("common.md").read_text(encoding="utf-8").strip()
    chapter_name = (role or "").strip().lower()
    if chapter_name not in _PHASE_CHAPTERS:
        return common
    chapter = _STANDING_ORDERS.joinpath(f"{chapter_name}.md").read_text(encoding="utf-8").strip()
    return f"{common}\n\n{chapter}"


def load_agents_md(working_dir: Path | None) -> str:
    """Load employee/install identity: ``.harness/AGENTS.md`` then cwd ``AGENTS.md``."""
    if working_dir is None:
        return ""
    root = working_dir.resolve()
    for candidate in (root / ".harness" / "AGENTS.md", root / "AGENTS.md"):
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8").strip()
        if text:
            return text
    return ""


@dataclass(frozen=True)
class StablePromptBlock:
    """Cache-stable standing orders; phase chapter selected by ``role``.

    Set ``include=False`` when a role manifest uses ``system_prompt_mode=
    "replace"`` so packaged standing orders are omitted.
    """

    role: str | None = None
    include: bool = True

    def render(self) -> str:
        if not self.include:
            return ""
        return _render_block("stable", packaged_standing_orders(role=self.role))


@dataclass(frozen=True)
class ContextPromptBlock:
    """Workspace catalogues and AGENTS.md (install profile / employee brief)."""

    workspace_governance: str
    skill_catalogue: str
    memory_catalogue: str
    agents_md: str = ""
    tool_catalogue: str = ""
    subagent_catalogue: str = ""

    def render(self) -> str:
        agents = (
            f"# AGENTS.md\n\n{self.agents_md.strip()}" if self.agents_md.strip() else ""
        )
        # Cursor pie order inside <context>: rules → tool defs → MCP/dynamic
        # → skills → memory → subagent definitions.
        content = _join(
            agents,
            self.workspace_governance,
            self.tool_catalogue,
            self.skill_catalogue,
            self.memory_catalogue,
            self.subagent_catalogue,
        )
        return _render_block("context", content) if content else ""

    def render_compact_catalogue_reference(self) -> str:
        """Non-instructional catalogue slices for compaction user context.

        Omits AGENTS.md, governance prose, tool schemas, and subagent
        definitions so workspace-controlled instructions cannot influence the
        rolling summary.
        """
        return _join(self.skill_catalogue, self.memory_catalogue)


def render_runtime_context(runtime_info: str) -> str:
    """Volatile host facts injected before the first user turn."""
    return _render_block("runtime-context", runtime_info)


def assemble_stable_context_prefix(
    *,
    stable: StablePromptBlock,
    context: ContextPromptBlock,
) -> str:
    """Render the cacheable stable+context prefix (no role addendum).

    Compaction summariser calls reuse a Dream-owned stable slice of this
    assembler so provider prompt-cache can hit across live turns and compact.
    """
    return "\n\n".join(block for block in (stable.render(), context.render()) if block)


def assemble_session_system_prompt(
    *,
    stable: StablePromptBlock,
    context: ContextPromptBlock,
    role_instructions: str | None = None,
) -> str:
    """Render the deterministic stable-first system-prompt sequence."""
    prefix = assemble_stable_context_prefix(stable=stable, context=context)
    role = _render_block("role", role_instructions) if role_instructions else ""
    if not role:
        return prefix
    if not prefix:
        return role
    return f"{prefix}\n\n{role}"


def _join(*parts: str | None) -> str:
    return "\n\n".join(part for part in parts if part)


def _render_block(name: str, content: str) -> str:
    return f"<{name}>\n{content}\n</{name}>"


__all__ = [
    "ContextPromptBlock",
    "StablePromptBlock",
    "assemble_session_system_prompt",
    "assemble_stable_context_prefix",
    "load_agents_md",
    "packaged_standing_orders",
    "render_runtime_context",
]
