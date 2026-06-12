"""Built-in tools shipped with the SDK."""

from __future__ import annotations

from dream.tools._registry import ToolRegistry, ToolSource
from dream.tools.builtin.bash import BashTool
from dream.tools.builtin.cron_list import CronListTool
from dream.tools.builtin.cron_show import CronShowTool
from dream.tools.builtin.file_edit import FileEditTool
from dream.tools.builtin.file_read import FileReadTool
from dream.tools.builtin.file_write import FileWriteTool
from dream.tools.builtin.git import GitTool
from dream.tools.builtin.memory_get import MemoryGetTool
from dream.tools.builtin.memory_search import MemorySearchTool
from dream.tools.builtin.observability_query import QueryLogsTool, QueryMetricsTool
from dream.tools.builtin.plan_show import PlanShowTool
from dream.tools.builtin.propose_memory import MemoryProposeTool
from dream.tools.builtin.read_offloaded import ReadOffloadedTool
from dream.tools.builtin.skill import SkillTool
from dream.tools.builtin.task_create import TaskCreateTool
from dream.tools.builtin.task_get import TaskGetTool
from dream.tools.builtin.task_output import TaskOutputTool
from dream.tools.builtin.task_stop import TaskStopTool
from dream.tools.builtin.working_memory import (
    WorkingMemoryAppendTool,
    WorkingMemoryReadTool,
    WorkingMemoryWriteTool,
)

# Canonical ordering for the model-facing tool schema. Stable across processes
# so prompt caches downstream actually hit; alphabetical within a "phase".
_DEFAULT_ORDER: tuple[str, ...] = (
    "read_file",
    "edit_file",
    "write_file",
    "bash",
    "git",
    "read_offloaded",
    "skill",
    "memory_search",
    "memory_get",
    "query_logs",
    "query_metrics",
    "task_create",
    "task_get",
    "task_output",
    "task_stop",
    "cron_list",
    "cron_show",
    "plan_show",
)


def default_registry() -> ToolRegistry:
    """Return a fresh ``ToolRegistry`` populated with the default tools."""
    registry = ToolRegistry(default_order=_DEFAULT_ORDER)
    registry.register(FileReadTool(), source=ToolSource.DEFAULT)
    registry.register(FileEditTool(), source=ToolSource.DEFAULT)
    registry.register(FileWriteTool(), source=ToolSource.DEFAULT)
    registry.register(BashTool(), source=ToolSource.DEFAULT)
    registry.register(GitTool(), source=ToolSource.DEFAULT)
    registry.register(ReadOffloadedTool(), source=ToolSource.DEFAULT)
    registry.register(SkillTool(), source=ToolSource.DEFAULT)
    registry.register(MemorySearchTool(), source=ToolSource.DEFAULT)
    registry.register(MemoryGetTool(), source=ToolSource.DEFAULT)
    registry.register(QueryLogsTool(), source=ToolSource.DEFAULT)
    registry.register(QueryMetricsTool(), source=ToolSource.DEFAULT)
    registry.register(TaskCreateTool(), source=ToolSource.DEFAULT)
    registry.register(TaskGetTool(), source=ToolSource.DEFAULT)
    registry.register(TaskOutputTool(), source=ToolSource.DEFAULT)
    registry.register(TaskStopTool(), source=ToolSource.DEFAULT)
    registry.register(CronListTool(), source=ToolSource.DEFAULT)
    registry.register(CronShowTool(), source=ToolSource.DEFAULT)
    registry.register(PlanShowTool(), source=ToolSource.DEFAULT)
    return registry


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


__all__ = ["default_registry", "register_task_memory_tools"]
