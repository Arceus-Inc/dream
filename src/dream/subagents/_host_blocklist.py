"""Host-side tool strip for subagents (Hermes DELEGATE_BLOCKED_TOOLS).

Children inherit ``tools ∩ parent`` then lose these host-forbidden names.
The model cannot widen past this strip.
"""

from __future__ import annotations

# Never available on leaf children. Orchestrators that declare ``spawnable`` keep
# ``spawn_subagent`` via the inline executor; everything else here is absolute.
HOST_BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        "clarify",
        "memory_search",
        "memory_get",
        "memory_propose",
        "working_memory_read",
        "working_memory_write",
        "working_memory_append",
        "send_message",
        "cron_list",
        "cron_show",
        "cron_create",
        "cron_delete",
        "cron_toggle",
        "remote_trigger",
        "task_create",
        "task_update",
        # Background shell tasks stay on the parent; children poll via
        # delegation_* only when the parent opted into background spawn.
        "enter_worktree",
        "exit_worktree",
    }
)

# Mutating file tools denied for Explore / Plan / Verify builtins.
READONLY_DENIED_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "apply_patch",
        "edit_file",
        "bash",  # can mutate; explore uses read/search only
        "git",
        "todo_write",
        "execute_code",
        "browser_run",
        "spawn_subagent",
        "plan_show",
    }
)

EXPLORE_TOOLS: tuple[str, ...] = (
    "read_file",
    "grep",
    "glob",
    "web_fetch",
    "web_search",
    "read_offloaded",
)

PLAN_TOOLS: tuple[str, ...] = (
    "read_file",
    "grep",
    "glob",
    "web_fetch",
    "read_offloaded",
)

VERIFY_TOOLS: tuple[str, ...] = (
    "read_file",
    "grep",
    "glob",
    "bash",
    "run_command",
    "read_offloaded",
    "web_fetch",
)


def strip_host_blocked(tools: tuple[str, ...], *, keep_spawn: bool) -> tuple[str, ...]:
    """Drop host-forbidden tools; optionally keep ``spawn_subagent``."""
    blocked = HOST_BLOCKED_TOOLS
    if not keep_spawn:
        blocked = blocked | frozenset({"spawn_subagent"})
    return tuple(name for name in tools if name not in blocked)


__all__ = [
    "EXPLORE_TOOLS",
    "HOST_BLOCKED_TOOLS",
    "PLAN_TOOLS",
    "READONLY_DENIED_TOOLS",
    "VERIFY_TOOLS",
    "strip_host_blocked",
]
