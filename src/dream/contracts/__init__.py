"""Cross-repo Protocols and types.

This subpackage is the only thing `chorus`, `lattice`, and `horizon` are
allowed to import from `dream` besides the top-level facade. It must stay
free of provider and I/O dependencies so consumers can depend on it
without pulling in `httpx`, `anthropic`, `openai`, etc.

The Protocols here describe shapes. Concrete implementations live
elsewhere in the SDK or in sibling repos.
"""

from dream.contracts.delegation import (
    CapacityPort,
    DelegatedIntakePort,
    DelegatedWorkRef,
    DelegatedWorkRequest,
    ProfessionCapacity,
    StaffingBlocked,
    StaffingRequirement,
)
from dream.contracts.exec_plan import ExecPlan, ExecPlanLedger, ExecPlanStatus
from dream.contracts.governance import (
    GovDecision,
    GovernancePort,
    GovernanceView,
    GovGoal,
    GovProposal,
)
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
from dream.contracts.strategy import (
    GoalNode,
    GoalStore,
    IntakePort,
    LandedOutcome,
    LandedPhase,
    OutcomeEvent,
    OutcomeFeed,
    Priority,
    RecoveryHint,
)
from dream.contracts.tool import Tool, ToolContext, ToolResult

# The cross-repo contract version — the single coordination point across
# dream, chorus, lattice, and horizon (chorus spec 05 §2). Follows semver:
# a breaking Protocol change here is a dream MAJOR bump and a coordinated
# sibling release. Internals beneath these Protocols may churn freely.
# 0.2.0: added the horizon strategy seam (IntakePort / GoalStore / OutcomeFeed) — additive. The
# module was named ``strategy.py`` (content-named, like every other contract); Decisions are
# horizon-native and deliberately never enter this seam (chorus only ever sees goals + tasks).
# 0.3.0: added the governance seam (GovernancePort + Gov* read shapes) — additive. The reverse edge of
# strategy: a chorus CEO employee's tools steer horizon's direction; horizon supplies the adapter.
# 0.4.0: added delegated intake, observed capacity, and outcome hierarchy shapes — additive.
# 0.5.0: added LandedPhase / LandedOutcome and outcome.landed fields on OutcomeEvent — additive.
__contract_version__ = "0.5.0"

__all__ = [
    "CapacityPort",
    "DelegatedIntakePort",
    "DelegatedWorkRef",
    "DelegatedWorkRequest",
    "ExecPlan",
    "ExecPlanLedger",
    "ExecPlanStatus",
    "GoalNode",
    "GoalStore",
    "GovDecision",
    "GovGoal",
    "GovProposal",
    "GovernancePort",
    "GovernanceView",
    "Hook",
    "HookEvent",
    "HookResult",
    "HookSpec",
    "IntakePort",
    "LandedOutcome",
    "LandedPhase",
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
    "ProfessionCapacity",
    "Provider",
    "ProviderCapabilities",
    "ProviderEvent",
    "ProviderUsage",
    "RecoveryHint",
    "Skill",
    "StaffingBlocked",
    "StaffingRequirement",
    "Tool",
    "ToolContext",
    "ToolResult",
    "__contract_version__",
]