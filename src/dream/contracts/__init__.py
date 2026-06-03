"""Cross-repo Protocols and types.

This subpackage is the only thing `chorus`, `lattice`, and `horizon` are
allowed to import from `dream` besides the top-level facade. It must stay
free of provider and I/O dependencies so consumers can depend on it
without pulling in `httpx`, `anthropic`, `openai`, etc.

The Protocols here describe shapes. Concrete implementations live
elsewhere in the SDK or in sibling repos.
"""

from dream.contracts.exec_plan import ExecPlan, ExecPlanLedger, ExecPlanStatus
from dream.contracts.hook import Hook, HookEvent, HookResult, HookSpec
from dream.contracts.memory import (
    MemoryDelta,
    MemoryRecord,
    MemoryScope,
    MemoryStore,
    MemoryType,
    MemoryWriter,
)
from dream.contracts.plugin import Plugin, PluginManifest
from dream.contracts.provider import (
    Provider,
    ProviderCapabilities,
    ProviderEvent,
    ProviderUsage,
)
from dream.contracts.skill import Skill
from dream.contracts.tool import Tool, ToolContext, ToolResult

__all__ = [
    "ExecPlan",
    "ExecPlanLedger",
    "ExecPlanStatus",
    "Hook",
    "HookEvent",
    "HookResult",
    "HookSpec",
    "MemoryDelta",
    "MemoryRecord",
    "MemoryScope",
    "MemoryStore",
    "MemoryType",
    "MemoryWriter",
    "Plugin",
    "PluginManifest",
    "Provider",
    "ProviderCapabilities",
    "ProviderEvent",
    "ProviderUsage",
    "Skill",
    "Tool",
    "ToolContext",
    "ToolResult",
]
