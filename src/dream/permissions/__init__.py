"""Permission decision core (Spec 13A).

A pure, synchronous allow/deny/ask layer: ``evaluate(request, policy)`` maps a
:class:`PermissionRequest` (what a tool call will do) plus a :class:`Policy`
(the active sandbox posture + operator rules) to a :class:`PermissionDecision`.
The credential guard is non-disableable and runs first; the four-tier model
subsumes the older permission-mode concept (no separate mode enum).
"""

from dream.permissions._checker import evaluate
from dream.permissions._command_patterns import BUILTIN_COMMAND_DENY
from dream.permissions._config import (
    SandboxConfig,
    SandboxConfigError,
    read_sandbox_config,
)
from dream.permissions._credential_guard import (
    BUILTIN_CREDENTIAL_PATTERNS,
    is_credential_path,
)
from dream.permissions._limits import SessionLimiter, SessionLimits
from dream.permissions._overrides import (
    TierOverrideError,
    TierOverrides,
    read_tier_overrides,
)
from dream.permissions._path_validator import validate_repo_write
from dream.permissions._policy_builder import PolicyAssembly, build_policy
from dream.permissions._tiers import DEFAULT_TIER
from dream.permissions._types import (
    Effect,
    Outcome,
    PathRule,
    PermissionDecision,
    PermissionRequest,
    Policy,
    SandboxTier,
)

__all__ = [
    "BUILTIN_COMMAND_DENY",
    "BUILTIN_CREDENTIAL_PATTERNS",
    "DEFAULT_TIER",
    "Effect",
    "Outcome",
    "PathRule",
    "PermissionDecision",
    "PermissionRequest",
    "Policy",
    "PolicyAssembly",
    "SandboxConfig",
    "SandboxConfigError",
    "SandboxTier",
    "SessionLimiter",
    "SessionLimits",
    "TierOverrideError",
    "TierOverrides",
    "build_policy",
    "evaluate",
    "is_credential_path",
    "read_sandbox_config",
    "read_tier_overrides",
    "validate_repo_write",
]
