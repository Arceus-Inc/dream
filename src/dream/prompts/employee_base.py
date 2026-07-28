"""Workforce Base Prompt — Hermes-style stable identity for org employees.

Hermes splits the system prompt into stable / context / volatile
(``agent/system_prompt.py``) and gates guidance on tool presence
(``MEMORY_GUIDANCE`` only if ``memory`` is available, etc.). Dream mirrors
that here for the AI Workforce:

* **stable** — this module (identity + tool-gated resume/recall/tool-choice)
* **context** — dream role base + Chorus craft brief (``## Operating brief``)
* **volatile** — runtime info, catalogues, roster/inbox (elsewhere)

Craft ``brief.py`` files stay employee-specific. Shared invariants live here.
"""

from __future__ import annotations

from collections.abc import Iterable

# SessionOptions.metadata key — Chorus / callers can force on/off per session.
EMPLOYEE_MODE_METADATA_KEY = "dream.employee_mode"

# Chorus write_role_overlays stamps this heading; used as a fallback detector.
_OPERATING_BRIEF_MARKER = "## Operating brief"

EMPLOYEE_BASE_PROMPT = (
    "You are an employee of a AI Workforce.\n"
    "\n"
    "You operate inside an isolated git worktree for your beat — prefer relative "
    "paths; do not leave that tree. Escalate blockers outside your worktree "
    "(permissions, secrets, org decisions) with a comment to your manager; do not "
    "guess. Leave finished craft changes for the harness lander (typically as a "
    "PR); never force-push. Under uncertainty, make the most reasonable call, "
    "record it, and keep going. Tools describe themselves; load deep procedure "
    "on demand via the `skill` tool when you have it."
)

RESUME_DIRECTIVE = (
    "RESUME, DON'T RESTART: keep a durable checklist with `todo_write` in `TODO.md`, check items "
    "off as you go, and read it first every beat — reconcile against git/artifacts, then continue "
    "unchecked steps. Never restart from scratch when checklist + work already sit in the worktree. "
    "Load `cross-beat-resume` via `skill` for the full protocol and budget-flush rules."
)

RECALL_DIRECTIVE = (
    "EPISODIC MEMORY: on resume beats (TODO.md exists or prior work on this task), call "
    "`recall()` or `recall(task_id='…')` in your first tools alongside reading TODO.md; "
    "`get_run(run_id='…')` for full prose. Outcomes are data — `incomplete` → continue; "
    "`needs_changes`/`blocked` → avoid. Load `cross-beat-recall` via `skill` for modes and "
    "debug profile."
)

TOOL_CHOICE_MATRIX = (
    "TOOL CHOICE (cheapest surface that fits):\n"
    "Use this                         Don't — use instead\n"
    "───────────────────────────────  ────────────────────────────────\n"
    "tool (read/write/run/lint/…)     spawn to wrap a single tool\n"
    "skill(name=…) for craft steps    invent procedure a skill covers\n"
    "spawn_subagent for named         spawn when tools+skills suffice\n"
    "  specialist / fresh judgment    spawn mechanical multi-step glue\n"
    "just implement yourself          durable across beats → TODO.md\n"
    "Rules: tool > skill > spawn. Spawn only for a typed specialist\n"
    "artifact you cannot honestly author alone."
)

# Surfaces that imply the Hermes Use/Don't matrix is relevant.
_TOOL_CHOICE_TRIGGERS = frozenset(
    {
        "skill",
        "spawn_subagent",
        "read_file",
        "write_file",
        "run_command",
        "todo_write",
        "repo_search",
    }
)


def render_employee_base_prompt(*, tool_names: Iterable[str] = ()) -> str:
    """Stable workforce identity plus Hermes-style tool-gated directives.

    Empty ``tool_names`` (e.g. toolless planner) yields identity only — no
    resume/recall/tool-choice that would invite unavailable tool calls.
    """
    tools = frozenset(tool_names)
    parts: list[str] = [EMPLOYEE_BASE_PROMPT]
    if "todo_write" in tools:
        parts.append(RESUME_DIRECTIVE)
    if "recall" in tools:
        parts.append(RECALL_DIRECTIVE)
    if tools & _TOOL_CHOICE_TRIGGERS:
        parts.append(TOOL_CHOICE_MATRIX)
    return "\n\n".join(parts)


def should_inject_employee_base(
    *,
    employee_mode: bool,
    metadata: dict[str, object] | None,
    system_prompt: str | None,
    is_subagent: bool,
) -> bool:
    """Whether this session should receive the workforce Base Prompt.

    Precedence: explicit metadata override → harness employee_mode →
    Operating-brief marker (Chorus overlay). Subagents stay lean (Hermes
    child = goal + context only).
    """
    if is_subagent:
        return False
    meta = metadata or {}
    if EMPLOYEE_MODE_METADATA_KEY in meta:
        return bool(meta[EMPLOYEE_MODE_METADATA_KEY])
    if employee_mode:
        return True
    return bool(system_prompt and _OPERATING_BRIEF_MARKER in system_prompt)


__all__ = [
    "EMPLOYEE_BASE_PROMPT",
    "EMPLOYEE_MODE_METADATA_KEY",
    "RECALL_DIRECTIVE",
    "RESUME_DIRECTIVE",
    "TOOL_CHOICE_MATRIX",
    "render_employee_base_prompt",
    "should_inject_employee_base",
]
