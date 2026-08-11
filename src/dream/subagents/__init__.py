"""Subagent layer — chorus-side declaration, registry, and projection.

A subagent is a capability-minimized, ephemeral teammate a beat spawns to do
bounded work, then dissolves. This package defines:

- ``Subagent``: the frozen declaration (on a role / shared registry).
- ``SubagentSet``: the resolved set of subagents available to a beat.
- ``SubagentRegistry``: the Tier-2 shared-capability agent registry.
- ``project_subagent``: the chorus→dream projection (Subagent → TeammateSpawnConfig).
"""

from dream.subagents._catalogue import SubagentCatalogue, SubagentCatalogueEntry
from dream.subagents._declaration import (
    GENERAL_PURPOSE_DESCRIPTION,
    GENERAL_PURPOSE_NAME,
    PermissionDelta,
    Subagent,
    SubagentSet,
)
from dream.subagents._projection import SubagentResult, project_subagent
from dream.subagents._registry import SubagentRegistry

__all__ = [
    "GENERAL_PURPOSE_DESCRIPTION",
    "GENERAL_PURPOSE_NAME",
    "PermissionDelta",
    "Subagent",
    "SubagentCatalogue",
    "SubagentCatalogueEntry",
    "SubagentRegistry",
    "SubagentResult",
    "SubagentSet",
    "project_subagent",
]
