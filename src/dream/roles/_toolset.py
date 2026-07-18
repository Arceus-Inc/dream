"""Capability minimisation (Spec 10 decision #8).

The minimum toolset is the actual set of tool names a role-bound dispatcher
will honour. It is the intersection of:

* the manifest's allow-list (``tools``; ``None`` means "all registered" and
  is reserved to the generator),
* the registered tool declarations (so an unknown name in the manifest is
  dropped — capability minimisation, not capability invention),
* the active sandbox tier (each tool's ``tier_required`` must be ``<=`` the
  tier),
* safe transport companions required to consume an allowed tool's output,
* minus the manifest's ``disallowed_tools`` (operator vetoes always win).
"""

from __future__ import annotations

from collections.abc import Mapping

from dream.permissions import SandboxTier
from dream.roles._manifest import RoleManifest
from dream.tools._base import ToolDeclaration


def compute_minimum_toolset(
    manifest: RoleManifest,
    *,
    sandbox_tier: SandboxTier,
    declarations: Mapping[str, ToolDeclaration],
) -> frozenset[str]:
    """Return the frozen set of tool names this role may invoke right now."""
    if manifest.tools is None:
        candidates: set[str] = set(declarations)
    else:
        candidates = {name for name in manifest.tools if name in declarations}

    if "read_file" in candidates and "read_offloaded" in declarations:
        candidates.add("read_offloaded")

    tier_value = int(sandbox_tier)
    within_tier = {name for name in candidates if declarations[name].tier_required <= tier_value}
    return frozenset(within_tier - set(manifest.disallowed_tools))
