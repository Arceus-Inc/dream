"""Assemble a :class:`Policy` from operator config (Spec 13B).

``build_policy`` reads the sandbox posture and trust-ramp overrides and combines
them with the caller-supplied *trusted built-in tiers* and the role's *tool-allow*
set into a ready-to-evaluate Policy. ``required_tier`` follows the two-class
model: built-in "trusted" tools keep their declared tier, operator overrides win
on conflict, and a discovered tool absent from both stays read-only (the
checker's default → ASK).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dream.config.paths import DreamPaths
from dream.permissions._config import read_sandbox_config
from dream.permissions._overrides import read_tier_overrides
from dream.permissions._types import Policy, SandboxTier
from dream.utils.clock import Clock, SystemClock


@dataclass(frozen=True)
class PolicyAssembly:
    """A built Policy plus warnings surfaced as data (e.g. stale promotions)."""

    policy: Policy
    warnings: tuple[str, ...]


def build_policy(
    paths: DreamPaths,
    *,
    cwd: Path,
    trusted_tiers: Mapping[str, SandboxTier],
    tool_allow: frozenset[str] | None = None,
    clock: Clock | None = None,
) -> PolicyAssembly:
    """Read operator config and assemble a Policy for the checker."""
    clock = clock or SystemClock()
    sandbox = read_sandbox_config(paths.sandbox_config())
    overrides = read_tier_overrides(paths.tool_tier_overrides(), clock=clock)
    required_tier = {**trusted_tiers, **overrides.required_tier}
    policy = Policy(
        tier=sandbox.tier,
        cwd=cwd,
        required_tier=required_tier,
        tool_allow=tool_allow if tool_allow is not None else frozenset(),
        credential_extra=sandbox.credential_extra,
        extra_allowed=tuple(_anchor(cwd, raw) for raw in sandbox.extra_allowed),
    )
    return PolicyAssembly(policy=policy, warnings=overrides.warnings)


def _anchor(cwd: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else cwd / path
