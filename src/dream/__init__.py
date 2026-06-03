"""dream - an SDK for building autonomous agent harnesses.

The public API is exactly what this module re-exports. Anything not
listed in `__all__` is private and may change without notice. The
re-exports are pinned by `tests/test_public_api.py`.
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
from dream.errors import (
    CompactionError,
    DreamError,
    HookError,
    PermissionError,
    PluginError,
    ProviderError,
    SandboxError,
)
from dream.events import (
    Compacted,
    Error,
    Event,
    HookBlocked,
    PermissionDenied,
    TextDelta,
    ToolUseResult,
    ToolUseStart,
    TurnComplete,
)
from dream.harness import Harness, HarnessConfig
from dream.session import Session, SessionCost, SessionOptions
from dream.types import MessageRole, StopReason

__version__ = "0.1.0"

__all__ = [
    # facade
    "Harness",
    "HarnessConfig",
    "Session",
    "SessionOptions",
    "SessionCost",
    # events
    "Event",
    "TextDelta",
    "ToolUseStart",
    "ToolUseResult",
    "TurnComplete",
    "Compacted",
    "HookBlocked",
    "PermissionDenied",
    "Error",
    # errors
    "DreamError",
    "ProviderError",
    "SandboxError",
    "PermissionError",
    "HookError",
    "PluginError",
    "CompactionError",
    # contracts: tool
    "Tool",
    "ToolResult",
    "ToolContext",
    # contracts: hook
    "Hook",
    "HookEvent",
    "HookResult",
    "HookSpec",
    # contracts: skill / plugin
    "Skill",
    "Plugin",
    "PluginManifest",
    # contracts: provider
    "Provider",
    "ProviderCapabilities",
    "ProviderEvent",
    "ProviderUsage",
    # contracts: memory
    "MemoryRecord",
    "MemoryDelta",
    "MemoryScope",
    "MemoryType",
    "MemoryStore",
    "MemoryWriter",
    # contracts: exec plan
    "ExecPlan",
    "ExecPlanLedger",
    "ExecPlanStatus",
    # types
    "MessageRole",
    "StopReason",
    # metadata
    "__version__",
]
