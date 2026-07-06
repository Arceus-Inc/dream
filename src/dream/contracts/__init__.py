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
from dream.contracts.horizon import (
    GoalNode,
    GoalStore,
    IntakePort,
    OutcomeEvent,
    OutcomeFeed,
    Priority,
)
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

# The cross-repo contract version — the single coordination point across
# dream, chorus, lattice, and horizon (chorus spec 05 §2). Follows semver:
# a breaking Protocol change here is a dream MAJOR bump and a coordinated
# sibling release. Internals beneath these Protocols may churn freely.
# 0.2.0: added the horizon seam (IntakePort / GoalStore / OutcomeFeed) — additive.
__contract_version__ = "0.2.0"

__all__ = [
    "ExecPlan",
    "ExecPlanLedger",
    "ExecPlanStatus",
    "GoalNode",
    "GoalStore",
    "Hook",
    "HookEvent",
    "HookResult",
    "HookSpec",
    "IntakePort",
    "MemoryDelta",
    "MemoryRecord",
    "MemoryScope",
    "MemoryStore",
    "MemoryType",
    "MemoryWriter",
    "OutcomeEvent",
    "OutcomeFeed",
    "Plugin",
    "PluginManifest",
    "Priority",
    "Provider",
    "ProviderCapabilities",
    "ProviderEvent",
    "ProviderUsage",
    "Skill",
    "Tool",
    "ToolContext",
    "ToolResult",
    "__contract_version__",
]