"""Merge hooks from explicit registrations and plugin contributions.

The harness owns direct registrations (``Harness.register_hook``);
plugins contribute via their :class:`dream.contracts.plugin.Plugin`
bundle. The loader is the one place those sources merge into the
executor's hook list, so load order (and therefore tie-break order
within a priority) is deterministic: harness first, then plugins in
load order.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from dream.contracts.hook import Hook
from dream.contracts.plugin import Plugin

__all__ = ["collect_hooks"]


def collect_hooks(
    harness_hooks: Sequence[Hook],
    plugins: Iterable[Plugin] = (),
) -> tuple[Hook, ...]:
    """Flatten harness + plugin hooks into one deterministic tuple."""
    merged: list[Hook] = list(harness_hooks)
    for plugin in plugins:
        merged.extend(plugin.hooks)
    return tuple(merged)
