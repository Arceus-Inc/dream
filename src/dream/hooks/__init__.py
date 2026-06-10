"""Hooks — fire-and-forget observers of the agent loop (spec 13).

The :class:`HookExecutor` dispatches lifecycle events
(:class:`dream.contracts.hook.HookEvent`) to registered hooks in
priority order, crash-isolated and deadline-bounded. Hooks never veto —
spec 13 divergence #1 strips OpenHarness's blocking path.
"""

from __future__ import annotations

from dream.hooks._executor import FireOutcome, HookExecutor
from dream.hooks._loader import collect_hooks

__all__ = ["FireOutcome", "HookExecutor", "collect_hooks"]
