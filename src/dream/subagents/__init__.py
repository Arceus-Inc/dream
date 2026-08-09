"""Subagent layer — declarations, builtins, inline delegate, async manager.

Live path: ``spawn_subagent`` → ``run_subagent_delegate`` → ``run_role``.
"""

from dream.subagents._async_delegation import (
    AsyncDelegationManager,
    DelegationCompletion,
    DelegationHandle,
    DelegationSnapshot,
    DelegationStatus,
)
from dream.subagents._builtins import (
    EXPLORE,
    GENERAL_PURPOSE,
    PLAN,
    VERIFY,
    builtin_agents,
    merge_builtins,
)
from dream.subagents._catalogue import SubagentCatalogue, SubagentCatalogueEntry
from dream.subagents._declaration import (
    GENERAL_PURPOSE_DESCRIPTION,
    GENERAL_PURPOSE_NAME,
    MAX_INLINE_NESTING,
    MAX_SUBAGENT_DEPTH,
    PermissionDelta,
    Subagent,
    SubagentSet,
)
from dream.subagents._isolation import IsolationMode
from dream.subagents._projection import SubagentResult, project_subagent
from dream.subagents._registry import SubagentRegistry

__all__ = [
    "GENERAL_PURPOSE_DESCRIPTION",
    "GENERAL_PURPOSE_NAME",
    "MAX_INLINE_NESTING",
    "MAX_SUBAGENT_DEPTH",
    "AsyncDelegationManager",
    "DelegationCompletion",
    "DelegationHandle",
    "DelegationSnapshot",
    "DelegationStatus",
    "EXPLORE",
    "GENERAL_PURPOSE",
    "IsolationMode",
    "PLAN",
    "PermissionDelta",
    "Subagent",
    "SubagentCatalogue",
    "SubagentCatalogueEntry",
    "SubagentRegistry",
    "SubagentResult",
    "SubagentSet",
    "VERIFY",
    "builtin_agents",
    "merge_builtins",
    "project_subagent",
]
