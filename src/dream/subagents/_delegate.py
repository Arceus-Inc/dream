"""Hermes-style delegate helpers — fresh-session prompt + summary budget.

Critics/verifiers should see ``goal`` + packed ``context`` only (context firewall),
not the parent transcript. Parent sees a budgeted summary (Hermes ``_apply_summary_budget``).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from dream.subagents._inline_executor import run_subagent_inline
from dream.subagents._projection import SubagentResult
from dream.utils.fs import atomic_write_text

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.runner._observer import RunTaskObserver
    from dream.subagents._declaration import Subagent

__all__ = [
    "DEFAULT_MAX_SUMMARY_CHARS",
    "apply_summary_budget",
    "build_child_prompt",
    "run_subagent_delegate",
]

DEFAULT_MAX_SUMMARY_CHARS = 24_000


def build_child_prompt(
    goal: str,
    context: str | None = None,
    *,
    workspace_path: str | None = None,
) -> str:
    """Hermes child inlet: goal + optional packed context — never parent history."""
    parts = [
        "You are a focused subagent working on a specific delegated task.",
        "",
        "YOUR TASK:",
        goal.strip(),
    ]
    if context and context.strip():
        parts.extend(["", "CONTEXT:", context.strip()])
    if workspace_path:
        parts.extend(["", f"WORKSPACE PATH: {workspace_path}"])
    parts.extend(
        [
            "",
            "Complete this task using the tools available to you.",
            "When finished, provide a clear, concise summary of what you did,",
            "what you found, files changed, and any issues. Keep the summary tight:",
            "the parent only sees this summary, not your intermediate tool I/O.",
        ]
    )
    return "\n".join(parts)


def apply_summary_budget(text: str, *, max_chars: int = DEFAULT_MAX_SUMMARY_CHARS) -> str:
    """Truncate to ``max_chars`` with head+tail keep (Hermes summary budget)."""
    if max_chars < 1:
        max_chars = DEFAULT_MAX_SUMMARY_CHARS
    if len(text) <= max_chars:
        return text
    # Ensure head+tail+marker fit.
    marker = "\n\n…[summary truncated for parent context]…\n\n"
    budget = max_chars - len(marker)
    if budget < 2:
        return text[:max_chars]
    head = budget // 2
    tail = budget - head
    return text[:head] + marker + text[-tail:]


def _safe_spill_basename(agent_name: str) -> str:
    """Sanitize agent name for spill filenames (no path separators)."""
    safe = re.sub(r"[^\w\-]+", "_", agent_name.strip())[:64]
    return safe or "subagent"


def _write_spill_file(spill_dir: Path, agent_name: str, full: str) -> Path:
    """Write full summary under spill_dir; reject path traversal."""
    spill_dir = spill_dir.resolve()
    spill_dir.mkdir(parents=True, exist_ok=True)
    spill_path = (
        spill_dir / f"{_safe_spill_basename(agent_name)}-{uuid.uuid4().hex[:8]}.txt"
    ).resolve()
    spill_path.relative_to(spill_dir)
    atomic_write_text(spill_path, full)
    return spill_path


async def run_subagent_delegate(
    agent: Subagent,
    *,
    goal: str,
    context: str | None = None,
    harness: Harness,
    parent_tools: frozenset[str] | None = None,
    spawn_counter: list[int] | None = None,
    tracer: object | None = None,
    observer: RunTaskObserver | None = None,
    working_dir: Path | str | None = None,
    spill_dir: Path | str | None = None,
    summary_budget: int = DEFAULT_MAX_SUMMARY_CHARS,
) -> SubagentResult:
    """Run a specialist in a fresh ``run_role`` session; return budgeted summary.

    History starts empty (``run_role`` / ``run_subagent_inline`` always mint a new
    session). The firewall is the prompt inlet: only ``goal`` + packed ``context``.

    ``spill_dir`` is the session scratch dir: an over-budget summary is written
    there, never into the caller's worktree. Without it the summary is truncated
    with no spill.
    """
    workspace = str(working_dir) if working_dir is not None else None
    prompt = build_child_prompt(goal, context, workspace_path=workspace)
    result = await run_subagent_inline(
        agent,
        prompt=prompt,
        harness=harness,
        parent_tools=parent_tools,
        spawn_counter=spawn_counter,
        tracer=tracer,
        observer=observer,
    )
    if not result.success:
        return result

    full = result.output
    summary = apply_summary_budget(full, max_chars=summary_budget)
    if summary == full:
        return result

    note = ""
    if spill_dir is not None:
        spill_path = _write_spill_file(Path(spill_dir) / "delegation", agent.name, full)
        note = f"\n\n[full summary spilled to {spill_path}]"

    return SubagentResult(
        name=result.name,
        output=summary + note,
        success=result.success,
        error=result.error,
        turns_used=result.turns_used,
        tool_calls=result.tool_calls,
        tool_errors=result.tool_errors,
        warning=result.warning,
    )
