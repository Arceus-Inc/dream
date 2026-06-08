"""Build a session permission gate from the tool registry + operator config.

Spec 13C.3 — the production wiring that turns a :class:`ToolRegistry` and the
operator's ``.harness`` config into a :data:`PermissionGate` the dispatcher
consults before every tool call.

The *trusted* tier of each tool comes from its own declaration (built-in tools
are verified, so their declared ``tier_required`` is honoured); discovered tools
absent from the registry stay read-only until promoted (the trust ramp, handled
inside :func:`build_policy`). Staleness/other warnings are returned as data for
the caller to surface — this module never logs.
"""

from __future__ import annotations

from pathlib import Path

from dream.config.paths import DreamPaths
from dream.engine._tool_dispatch import PermissionGate
from dream.permissions import (
    PermissionDecision,
    PermissionRequest,
    SandboxTier,
    build_policy,
    evaluate,
)
from dream.tools._registry import ToolRegistry
from dream.utils.clock import Clock


def make_permission_gate(
    registry: ToolRegistry,
    *,
    paths: DreamPaths,
    cwd: Path,
    tool_allow: frozenset[str] | None = None,
    clock: Clock | None = None,
) -> tuple[PermissionGate, tuple[str, ...]]:
    """Assemble the session policy and return ``(gate, warnings)``."""
    trusted_tiers = {
        tool.name: _tier_from_int(tool.declaration.tier_required)
        for tool in registry.list_tools()
    }
    assembly = build_policy(
        paths, cwd=cwd, trusted_tiers=trusted_tiers, tool_allow=tool_allow, clock=clock
    )
    policy = assembly.policy

    def gate(request: PermissionRequest) -> PermissionDecision:
        return evaluate(request, policy)

    return gate, assembly.warnings


def _tier_from_int(value: int) -> SandboxTier:
    """Map a tool's declared integer tier to a SandboxTier, defaulting safe."""
    for tier in SandboxTier:
        if tier.value == value:
            return tier
    return SandboxTier.READ_ONLY


__all__ = ["make_permission_gate"]
