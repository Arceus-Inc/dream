"""Hooks — lifecycle observers/interceptors of the agent loop (spec 13).

The :class:`HookExecutor` dispatches lifecycle events
(:class:`dream.contracts.hook.HookEvent`) to registered hooks in
priority order, crash-isolated and deadline-bounded.

Observers by default. Opt-in powers via ``HookSpec``:

- ``allow_block`` — real PRE_TOOL_USE veto (Hermes ``pre_tool_call``)
- ``allow_continue`` — STOP continue nudge (Hermes ``pre_verify``)

Built-ins: :class:`VerifyOnStopHook`, :class:`ToolDenyListHook`.
"""

from __future__ import annotations

from dream.hooks._executor import FireOutcome, HookExecutor
from dream.hooks._loader import collect_hooks
from dream.hooks._tool_deny import ToolDenyListConfig, ToolDenyListHook
from dream.hooks._verify_on_stop import VerifyOnStopConfig, VerifyOnStopHook

__all__ = [
    "FireOutcome",
    "HookExecutor",
    "ToolDenyListConfig",
    "ToolDenyListHook",
    "VerifyOnStopConfig",
    "VerifyOnStopHook",
    "collect_hooks",
]
