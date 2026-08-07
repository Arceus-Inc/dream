"""Built-in tools shipped with the SDK.

``default_registry`` is the Level-2 coding surface (Spec 05 core + grep/glob/
todo_write/skill). Everything else is an opt-in pack registered via the
``register_*_tools`` helpers (wired from ``build_harness`` flags).
"""

from __future__ import annotations

from dream.tools._registry import ToolCollisionError, ToolRegistry, ToolSource
from dream.tools.builtin.apply_patch import ApplyPatchTool
from dream.tools.builtin.bash import BashTool
from dream.tools.builtin.browser_run import BrowserRunTool
from dream.tools.builtin.cron_create import CronCreateTool
from dream.tools.builtin.cron_delete import CronDeleteTool
from dream.tools.builtin.cron_list import CronListTool
from dream.tools.builtin.cron_show import CronShowTool
from dream.tools.builtin.cron_toggle import CronToggleTool
from dream.tools.builtin.enter_worktree import EnterWorktreeTool
from dream.tools.builtin.execute_code import ExecuteCodeTool
from dream.tools.builtin.exit_worktree import ExitWorktreeTool
from dream.tools.builtin.file_read import FileReadTool
from dream.tools.builtin.file_write import FileWriteTool
from dream.tools.builtin.git import GitTool
from dream.tools.builtin.glob import GlobTool
from dream.tools.builtin.grep import GrepTool
from dream.tools.builtin.lsp import LspTool
from dream.tools.builtin.memory_get import MemoryGetTool
from dream.tools.builtin.memory_search import MemorySearchTool
from dream.tools.builtin.observability_query import QueryLogsTool, QueryMetricsTool
from dream.tools.builtin.plan_show import PlanShowTool
from dream.tools.builtin.propose_memory import MemoryProposeTool
from dream.tools.builtin.read_offloaded import ReadOffloadedTool
from dream.tools.builtin.remote_trigger import RemoteTriggerTool
from dream.tools.builtin.session_search import SessionSearchTool
from dream.tools.builtin.skill import SkillTool
from dream.tools.builtin.spawn_subagent import SpawnSubagentTool
from dream.tools.builtin.task_create import TaskCreateTool
from dream.tools.builtin.task_get import TaskGetTool
from dream.tools.builtin.task_output import TaskOutputTool
from dream.tools.builtin.task_stop import TaskStopTool
from dream.tools.builtin.task_update import TaskUpdateTool
from dream.tools.builtin.todo_write import TodoWriteTool
from dream.tools.builtin.web_extract import WebExtractTool
from dream.tools.builtin.web_fetch import WebFetchTool
from dream.tools.builtin.web_search import WebSearchTool
from dream.tools.builtin.working_memory import (
    WorkingMemoryAppendTool,
    WorkingMemoryReadTool,
    WorkingMemoryWriteTool,
)

# Level-2 coding surface: Spec 05 core + medium coding companions.
# ``apply_patch`` supersedes the former ``edit_file`` substring tool.
LEVEL2_ORDER: tuple[str, ...] = (
    "read_file",
    "apply_patch",
    "write_file",
    "bash",
    "git",
    "read_offloaded",
    "glob",
    "grep",
    "todo_write",
    "skill",
)

# Full canonical order so pack registrations stay byte-stable when opted in.
_FULL_ORDER: tuple[str, ...] = (
    *LEVEL2_ORDER,
    "lsp",
    "memory_search",
    "memory_get",
    "session_search",
    "query_logs",
    "query_metrics",
    "task_create",
    "task_get",
    "task_output",
    "task_stop",
    "task_update",
    "cron_list",
    "cron_show",
    "cron_create",
    "cron_delete",
    "cron_toggle",
    "remote_trigger",
    "enter_worktree",
    "exit_worktree",
    "plan_show",
    "web_search",
    "web_extract",
    "web_fetch",
    "browser_run",
    "execute_code",
)

# Back-compat alias for callers / tests that still import the old name.
_DEFAULT_ORDER = LEVEL2_ORDER


def _register(registry: ToolRegistry, tool: object) -> None:
    """Register ``tool`` if absent (idempotent pack registration)."""
    from dream.tools._base import BaseTool

    if not isinstance(tool, BaseTool):
        raise TypeError(f"expected BaseTool, got {type(tool)!r}")
    existing = registry.get(tool.name)
    if existing is not None:
        if (
            registry.source_for(tool.name) is ToolSource.DEFAULT
            and type(existing) is type(tool)
        ):
            return
        raise ToolCollisionError(
            f"tool name {tool.name!r} already registered and cannot be shadowed by a pack"
        )
    registry.register(tool, source=ToolSource.DEFAULT)


def default_registry() -> ToolRegistry:
    """Return a fresh registry with the Level-2 coding tools only."""
    registry = ToolRegistry(default_order=_FULL_ORDER)
    registry.register(FileReadTool(), source=ToolSource.DEFAULT)
    registry.register(ApplyPatchTool(), source=ToolSource.DEFAULT)
    registry.register(FileWriteTool(), source=ToolSource.DEFAULT)
    registry.register(BashTool(), source=ToolSource.DEFAULT)
    registry.register(GitTool(), source=ToolSource.DEFAULT)
    registry.register(ReadOffloadedTool(), source=ToolSource.DEFAULT)
    registry.register(GlobTool(), source=ToolSource.DEFAULT)
    registry.register(GrepTool(), source=ToolSource.DEFAULT)
    registry.register(TodoWriteTool(), source=ToolSource.DEFAULT)
    registry.register(SkillTool(), source=ToolSource.DEFAULT)
    return registry


def register_memory_tools(registry: ToolRegistry) -> None:
    """Register durable workspace memory + episodic session search (Spec 11)."""
    _register(registry, MemorySearchTool())
    _register(registry, MemoryGetTool())
    _register(registry, SessionSearchTool())


def register_task_tools(registry: ToolRegistry) -> None:
    """Register background-task tools (Spec 07)."""
    _register(registry, TaskCreateTool())
    _register(registry, TaskGetTool())
    _register(registry, TaskOutputTool())
    _register(registry, TaskStopTool())
    _register(registry, TaskUpdateTool())


def register_cron_tools(registry: ToolRegistry) -> None:
    """Register cron + remote-trigger tools (Spec 07)."""
    _register(registry, CronListTool())
    _register(registry, CronShowTool())
    _register(registry, CronCreateTool())
    _register(registry, CronDeleteTool())
    _register(registry, CronToggleTool())
    _register(registry, RemoteTriggerTool())


def register_web_tools(registry: ToolRegistry) -> None:
    """Register web search / extract / fetch tools."""
    _register(registry, WebSearchTool())
    _register(registry, WebExtractTool())
    _register(registry, WebFetchTool())


def register_browser_tools(registry: ToolRegistry) -> None:
    """Register the CDP browser tool."""
    _register(registry, BrowserRunTool())


def register_observability_tools(registry: ToolRegistry) -> None:
    """Register query_logs / query_metrics (Spec 12)."""
    _register(registry, QueryLogsTool())
    _register(registry, QueryMetricsTool())


def register_worktree_tools(registry: ToolRegistry) -> None:
    """Register enter/exit worktree tools."""
    _register(registry, EnterWorktreeTool())
    _register(registry, ExitWorktreeTool())


def register_code_intel_tools(registry: ToolRegistry) -> None:
    """Register lsp + execute_code."""
    _register(registry, LspTool())
    _register(registry, ExecuteCodeTool())


def register_plan_tools(registry: ToolRegistry) -> None:
    """Register plan_show."""
    _register(registry, PlanShowTool())


def register_legacy_surface(registry: ToolRegistry) -> None:
    """Register every former default-registry pack (migration escape hatch)."""
    register_memory_tools(registry)
    register_observability_tools(registry)
    register_task_tools(registry)
    register_cron_tools(registry)
    register_worktree_tools(registry)
    register_plan_tools(registry)
    register_web_tools(registry)
    register_browser_tools(registry)
    register_code_intel_tools(registry)


def register_task_memory_tools(registry: ToolRegistry) -> None:
    """Register the opt-in task-memory tools (spec 11a) into ``registry``.

    Kept out of :func:`default_registry` so the default tool surface is
    unchanged unless ``build_harness(working_memory=True)`` opts in. Idempotent:
    a no-op if already present, so a caller-supplied registry reused across
    ``build_harness`` calls does not collide.
    """
    if registry.get("working_memory_read") is not None:
        return
    registry.register(WorkingMemoryReadTool(), source=ToolSource.DEFAULT)
    registry.register(WorkingMemoryWriteTool(), source=ToolSource.DEFAULT)
    registry.register(WorkingMemoryAppendTool(), source=ToolSource.DEFAULT)
    registry.register(MemoryProposeTool(), source=ToolSource.DEFAULT)


__all__ = [
    "LEVEL2_ORDER",
    "SpawnSubagentTool",
    "default_registry",
    "register_browser_tools",
    "register_code_intel_tools",
    "register_cron_tools",
    "register_legacy_surface",
    "register_memory_tools",
    "register_observability_tools",
    "register_plan_tools",
    "register_task_memory_tools",
    "register_task_tools",
    "register_web_tools",
    "register_worktree_tools",
]
