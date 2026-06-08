"""The ordered permission-decision pipeline (Spec 13A).

A pure function — ``evaluate(request, policy) -> PermissionDecision`` — whose
step order is fixed in code (Approach A). The credential guard runs first and
cannot be disabled by any tier or allow-list. Grounded in OpenHarness's
``PermissionChecker.evaluate`` order, adapted to the four-tier model and the
tri-state outcome:

    1. credential guard (non-disableable)   6. unrestricted tier allows
    2. tool deny-list                       7. read-only, non-network allowed
    3. tool allow-list                      8. effectful: session -> trust -> boundary
    4. path deny rules                      9. default ask
    5. command deny

Two limiters are kept distinct: the *session tier* is a hard ceiling (DENY),
while the per-tool *trust* level is escalatable (ASK — an operator can promote
the tool). With no approver wired, the caller collapses ASK to a deny.
"""

from __future__ import annotations

import os
from pathlib import Path

from dream.permissions._command_patterns import BUILTIN_COMMAND_DENY
from dream.permissions._credential_guard import is_credential_path
from dream.permissions._globs import glob_to_regex
from dream.permissions._path_validator import validate_repo_write
from dream.permissions._types import (
    Effect,
    Outcome,
    PermissionDecision,
    PermissionRequest,
    Policy,
    SandboxTier,
)


def evaluate(request: PermissionRequest, policy: Policy) -> PermissionDecision:
    """Map a request + policy to an allow/deny/ask decision (pure, no IO except path resolution)."""
    # 1. Credential guard — first, non-disableable.
    for path in request.target_paths:
        if is_credential_path(path, policy.cwd, policy.credential_extra):
            return _decide(Outcome.DENY, f"credential-path guard: {path}", "credential_guard")

    # 2. Tool deny-list.
    if request.tool_name in policy.tool_deny:
        return _decide(Outcome.DENY, f"tool {request.tool_name!r} is deny-listed", "tool_deny")

    # 3. Tool allow-list (operator override; the guard has already run).
    if request.tool_name in policy.tool_allow:
        return _decide(Outcome.ALLOW, f"tool {request.tool_name!r} is allow-listed", "tool_allow")

    # 4. Path deny rules.
    for path in request.target_paths:
        for rule in policy.path_deny:
            if not rule.allow and _matches_path(rule.pattern, path, policy.cwd):
                return _decide(
                    Outcome.DENY, f"path deny rule {rule.pattern!r}: {path}", "path_deny"
                )

    # 5. Command deny (applies even at the unrestricted tier).
    if request.command is not None:
        for pattern in (*BUILTIN_COMMAND_DENY, *policy.command_deny):
            if pattern.search(request.command) is not None:
                return _decide(
                    Outcome.DENY, f"command-deny matched {pattern.pattern!r}", "command_deny"
                )

    # 6. Unrestricted tier allows everything that survived the denies above.
    if policy.tier is SandboxTier.UNRESTRICTED:
        return _decide(Outcome.ALLOW, "unrestricted tier", "tier_unrestricted")

    # 7. A read-only, non-network action is always allowed.
    if request.is_read_only and request.network_host is None:
        return _decide(Outcome.ALLOW, "read-only action", "read_only")

    # 8. Effectful action: gate by session tier, then tool trust, then write boundary.
    effects = _effects(request)
    for effect in effects:
        need = effect.required_tier
        if policy.tier < need:
            return _decide(
                Outcome.DENY,
                f"session tier {policy.tier.name.lower()} forbids {effect.label}",
                "tier_session",
            )
        trusted = policy.required_tier.get(request.tool_name, SandboxTier.READ_ONLY)
        if trusted < need:
            return _decide(
                Outcome.ASK,
                f"tool {request.tool_name!r} not trusted for {effect.label}; "
                "promote in tool-tier-overrides",
                "tier_trust",
            )

    if Effect.WRITE in effects:
        for path in request.target_paths:
            ok, reason = validate_repo_write(path, policy.cwd, policy.extra_allowed)
            if not ok:
                return _decide(Outcome.DENY, reason, "path_boundary")

    if effects:
        return _decide(Outcome.ALLOW, "permitted by tier", "tier_grant")

    # 9. An effectful action we can't characterise — fail safe to ask.
    return _decide(Outcome.ASK, "no rule matched", "default")


def _effects(request: PermissionRequest) -> tuple[Effect, ...]:
    effects: list[Effect] = []
    if request.network_host is not None:
        effects.append(Effect.NETWORK)
    if request.target_paths and not request.is_read_only:
        effects.append(Effect.WRITE)
    return tuple(effects)


def _matches_path(pattern: str, path: Path, cwd: Path) -> bool:
    regex = glob_to_regex(pattern)
    return any(regex.fullmatch(form) is not None for form in _path_forms(path, cwd))


def _path_forms(path: Path, cwd: Path) -> tuple[str, ...]:
    """An absolute and (when in-tree) a cwd-relative POSIX form to match against."""
    target = path.expanduser()
    if not target.is_absolute():
        target = cwd / target
    absolute = Path(os.path.normpath(target.as_posix()))
    cwd_abs = Path(os.path.normpath(cwd.as_posix()))
    if absolute == cwd_abs or absolute.is_relative_to(cwd_abs):
        return (absolute.as_posix(), absolute.relative_to(cwd_abs).as_posix())
    return (absolute.as_posix(),)


def _decide(outcome: Outcome, reason: str, rule: str) -> PermissionDecision:
    return PermissionDecision(outcome=outcome, reason=reason, rule=rule)
