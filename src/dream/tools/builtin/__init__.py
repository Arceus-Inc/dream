"""Built-in tools shipped with the SDK."""

from __future__ import annotations

from dream.tools._registry import ToolRegistry, ToolSource
from dream.tools.builtin.bash import BashTool
from dream.tools.builtin.file_edit import FileEditTool
from dream.tools.builtin.file_read import FileReadTool
from dream.tools.builtin.file_write import FileWriteTool
from dream.tools.builtin.git import GitTool
from dream.tools.builtin.observability_query import QueryLogsTool, QueryMetricsTool
from dream.tools.builtin.read_offloaded import ReadOffloadedTool
from dream.tools.builtin.skill import SkillTool

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
    "query_logs",
    "query_metrics",
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
    registry.register(QueryLogsTool(), source=ToolSource.DEFAULT)
    registry.register(QueryMetricsTool(), source=ToolSource.DEFAULT)
    return registry


__all__ = ["default_registry"]
