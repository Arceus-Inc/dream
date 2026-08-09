"""dream - an SDK for building autonomous agent harnesses.

The public API is exactly what this module re-exports. Anything not
listed in `__all__` is private and may change without notice. The
re-exports are pinned by `tests/test_public_api.py`.
"""

from dream._factory import build_harness
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
from dream.engine._cost import UsageSnapshot
from dream.errors import (
    CompactionError,
    DreamError,
    HookError,
    PermissionError,
    PluginError,
    ProviderError,
    RunTaskError,
    SandboxError,
    SessionResumeError,
    TaskCancelled,
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
from dream.observability import RunTrace, tail_events
from dream.replay import (
    DuplicateHarnessVariantIdError,
    DuplicateReplayCaseIdError,
    HarnessRevisionRef,
    HarnessVariant,
    IncompleteReplayResultError,
    InvalidReplayUsageError,
    ModelConfigRef,
    ReplayArtifactRef,
    ReplayAssertion,
    ReplayAssertionOutcome,
    ReplayCase,
    ReplayCaseOutcome,
    ReplayComparator,
    ReplayComparison,
    ReplayExecution,
    ReplayExecutor,
    ReplayIdentityMismatchError,
    ReplayOutcome,
    ReplayOutcomeKind,
    SandboxProfileRef,
    SessionCostDelta,
    SkillRevisionRef,
    ToolProfileRef,
)
from dream.services.session_store import (
    FileSessionStore,
    SessionHandle,
    SessionSnapshot,
)
from dream.session import Session, SessionCost, SessionOptions
from dream.types import MessageRole, StopReason

__version__ = "0.1.0"

__all__ = [
    "Compacted",
    "CompactionError",
    "DreamError",
    "DuplicateHarnessVariantIdError",
    "DuplicateReplayCaseIdError",
    "Error",
    "Event",
    "ExecPlan",
    "ExecPlanLedger",
    "ExecPlanStatus",
    "FileSessionStore",
    "Harness",
    "HarnessConfig",
    "HarnessRevisionRef",
    "HarnessVariant",
    "Hook",
    "HookBlocked",
    "HookError",
    "HookEvent",
    "HookResult",
    "HookSpec",
    "IncompleteReplayResultError",
    "InvalidReplayUsageError",
    "MemoryDelta",
    "MemoryRecord",
    "MemoryScope",
    "MemoryStore",
    "MemoryType",
    "MemoryWriter",
    "MessageRole",
    "ModelConfigRef",
    "PermissionDenied",
    "PermissionError",
    "Plugin",
    "PluginError",
    "PluginManifest",
    "Provider",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderEvent",
    "ProviderUsage",
    "ReplayArtifactRef",
    "ReplayAssertion",
    "ReplayAssertionOutcome",
    "ReplayCase",
    "ReplayCaseOutcome",
    "ReplayComparator",
    "ReplayComparison",
    "ReplayExecution",
    "ReplayExecutor",
    "ReplayIdentityMismatchError",
    "ReplayOutcome",
    "ReplayOutcomeKind",
    "RunTaskError",
    "RunTrace",
    "SandboxError",
    "SandboxProfileRef",
    "Session",
    "SessionCost",
    "SessionCostDelta",
    "SessionHandle",
    "SessionOptions",
    "SessionResumeError",
    "SessionSnapshot",
    "Skill",
    "SkillRevisionRef",
    "StopReason",
    "TaskCancelled",
    "TextDelta",
    "Tool",
    "ToolContext",
    "ToolProfileRef",
    "ToolResult",
    "ToolUseResult",
    "ToolUseStart",
    "TurnComplete",
    "UsageSnapshot",
    "__version__",
    "build_harness",
    "tail_events",
]
