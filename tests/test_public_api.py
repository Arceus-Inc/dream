"""Pin the public API surface.

`dream/__init__.py` is the contract. If a symbol disappears or shows up
without being intentionally added to `EXPECTED_PUBLIC_API` below, this
test fails and a CHANGELOG entry is required.
"""

from __future__ import annotations

import dream

EXPECTED_PUBLIC_API: frozenset[str] = frozenset({
    # facade
    "Harness", "HarnessConfig",
    "Session", "SessionOptions", "SessionCost",
    # events
    "Event", "TextDelta", "ToolUseStart", "ToolUseResult", "TurnComplete",
    "Compacted", "HookBlocked", "PermissionDenied", "Error",
    # errors
    "DreamError", "ProviderError", "SandboxError", "PermissionError",
    "HookError", "PluginError", "CompactionError",
    # contracts
    "Tool", "ToolResult", "ToolContext",
    "Hook", "HookEvent", "HookResult", "HookSpec",
    "Skill",
    "Plugin", "PluginManifest",
    "Provider", "ProviderCapabilities", "ProviderEvent", "ProviderUsage",
    "MemoryRecord", "MemoryDelta", "MemoryScope", "MemoryType",
    "MemoryStore", "MemoryWriter",
    "ExecPlan", "ExecPlanLedger", "ExecPlanStatus",
    # types
    "MessageRole", "StopReason",
    # factory
    "build_harness",
    # runtime (spec 15 P1) + control plane (P2)
    "Runtime", "RuntimeConfig", "RuntimeBusyError", "RuntimeBootBlockedError",
    "tail_events",
    # metadata
    "__version__",
})


def test_all_matches_expected() -> None:
    assert frozenset(dream.__all__) == EXPECTED_PUBLIC_API


def test_module_exposes_all() -> None:
    exposed = set(dir(dream))
    missing = EXPECTED_PUBLIC_API - exposed
    assert not missing, f"declared in __all__ but not present on module: {sorted(missing)}"


def test_version_is_string() -> None:
    assert isinstance(dream.__version__, str)
    assert dream.__version__.count(".") >= 2
