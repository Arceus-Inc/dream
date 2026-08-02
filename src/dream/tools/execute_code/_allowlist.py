"""Sandbox allowlist ∩ session tools (fail closed on empty intersection)."""

from __future__ import annotations

from dream.tools.execute_code._types import NestedToolName

# Full PTC surface for Dream. Intersection with the live session registry
# determines which stubs are generated — never fall open to the full set.
SANDBOX_ALLOWLIST: frozenset[NestedToolName] = frozenset(NestedToolName)


def sandbox_tools_for(session_tool_names: frozenset[str]) -> frozenset[NestedToolName]:
    """Return allowlisted tools present in the session.

    Unlike Hermes (which falls back to the full allowlist when the
    intersection is empty), Dream fails closed: empty → empty.
    """
    return frozenset(
        name for name in SANDBOX_ALLOWLIST if name.value in session_tool_names
    )


__all__ = ["SANDBOX_ALLOWLIST", "sandbox_tools_for"]
