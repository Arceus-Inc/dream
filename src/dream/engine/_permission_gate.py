"""Build a session permission gate from the tool registry + operator config.

Spec 13C.3 — the production wiring that turns a :class:`ToolRegistry` and the
operator's ``.harness`` config into a :data:`PermissionGate` the dispatcher
consults before every tool call.

The *trusted* tier of a tool comes from its own declaration only when it is a
vetted built-in (``ToolSource.is_builtin``); discovered tools (per-repo, skill,
MCP) are withheld from the trusted map by :func:`_trusted_tiers`, so they stay
read-only until an operator promotes them in tool-tier-overrides (the trust
ramp). Staleness/other warnings are returned as data for the caller to surface
— this module never logs.
"""

from __future__ import annotations

import functools
from pathlib import Path

from dream.config.paths import DreamPaths
from dream.engine._tool_dispatch import PermissionGate
from dream.permissions import (
    SandboxTier,
    build_policy,
    evaluate,
)
from dream.roles import RoleManifest, compute_minimum_toolset
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
    trusted_tiers = _trusted_tiers(registry)
    assembly = build_policy(
        paths, cwd=cwd, trusted_tiers=trusted_tiers, tool_allow=tool_allow, clock=clock
    )
    policy = assembly.policy

    gate = functools.partial(evaluate, policy=policy)
    return gate, assembly.warnings


def compute_session_role_allowlist(
    registry: ToolRegistry,
    *,
    paths: DreamPaths,
    cwd: Path,
    manifest: RoleManifest,
    clock: Clock | None = None,
) -> frozenset[str]:
    """Probe the active sandbox tier and intersect it with the role manifest.

    Returned set is the dispatcher's hard allow-list (Spec 10 decision #8) —
    a role cannot dispatch a tool outside this set even with an allow-all gate.

    Do NOT feed this set to :func:`make_permission_gate` as ``tool_allow``:
    ``tool_allow`` is an *allow-list* that short-circuits the checker to
    ``ALLOW`` (it returns early, before the path-deny / command-deny / tier /
    trust steps). Using it for role enforcement would *widen* a role tool past
    those guards (e.g. ``bash`` could run ``rm -rf /``) rather than restrict it.
    Role enforcement is a "must be in set" deny and belongs in the dispatcher
    (``EngineToolDispatcher.role_allowed_tools``); the gate must still apply its
    full pipeline to every role-allowed tool.
    """
    trusted_tiers = _trusted_tiers(registry)
    assembly = build_policy(
        paths, cwd=cwd, trusted_tiers=trusted_tiers, tool_allow=None, clock=clock
    )
    declarations = {tool.name: tool.declaration for tool in registry.list_tools()}
    return compute_minimum_toolset(
        manifest, sandbox_tier=assembly.policy.tier, declarations=declarations
    )


def _trusted_tiers(registry: ToolRegistry) -> dict[str, SandboxTier]:
    """Declared tiers for *vetted built-in* tools only.

    Discovered tools (per-repo, skill, MCP) are deliberately omitted: a tool
    absent from this map falls to the checker's READ_ONLY trust default (the
    trust ramp on :class:`Policy`), so a discovered tool that declares a
    mutating tier does not inherit it. Operators promote such tools explicitly
    via tool-tier-overrides, which :func:`build_policy` merges on top.
    """
    return {
        tool.name: _tier_from_int(tool.declaration.tier_required)
        for tool, source in registry.iter_with_source()
        if source.is_builtin
    }


def _tier_from_int(value: int) -> SandboxTier:
    """Map a tool's declared integer tier to a SandboxTier, defaulting safe."""
    for tier in SandboxTier:
        if tier.value == value:
            return tier
    return SandboxTier.READ_ONLY


__all__ = ["compute_session_role_allowlist", "make_permission_gate"]
