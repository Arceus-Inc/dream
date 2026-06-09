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
from dream.utils.paths import canonical_path_forms


def evaluate(request: PermissionRequest, policy: Policy) -> PermissionDecision:
    """Map a request + policy to an allow/deny/ask decision (pure, no IO except path resolution).

    A short ordered scan: each ``_step_*`` returns the first matching decision,
    or ``None`` to defer to the next step. The step order is the fixed Spec 13A
    pipeline (credential guard first, default-ask last).
    """
    for step in _PIPELINE:
        decision = step(request, policy)
        if decision is not None:
            return decision
    # An effectful action we can't characterise — fail safe to ask.
    return PermissionDecision(outcome=Outcome.ASK, reason="no rule matched", rule="default")


def _step_credential_guard(
    request: PermissionRequest, policy: Policy
) -> PermissionDecision | None:
    """1. Credential guard — first, non-disableable."""
    for path in request.target_paths:
        if is_credential_path(path, policy.cwd, policy.credential_extra):
            return PermissionDecision(
                outcome=Outcome.DENY,
                reason=f"credential-path guard: {path}",
                rule="credential_guard",
            )
    return None


def _step_tool_deny(request: PermissionRequest, policy: Policy) -> PermissionDecision | None:
    """2-3. Tool deny-list, with the allow-list as an *override for tool-deny only*.

    The allow-list does NOT short-circuit to ALLOW: command/path denies, tier
    checks, and the write boundary below still apply to allow-listed tools.
    """
    tool_allowed = request.tool_name in policy.tool_allow
    if request.tool_name in policy.tool_deny and not tool_allowed:
        return PermissionDecision(
            outcome=Outcome.DENY,
            reason=f"tool {request.tool_name!r} is deny-listed",
            rule="tool_deny",
        )
    return None


def _step_path_deny(request: PermissionRequest, policy: Policy) -> PermissionDecision | None:
    """4. Path deny rules."""
    for path in request.target_paths:
        for rule in policy.path_deny:
            if not rule.allow and _matches_path(rule.pattern, path, policy.cwd):
                return PermissionDecision(
                    outcome=Outcome.DENY,
                    reason=f"path deny rule {rule.pattern!r}: {path}",
                    rule="path_deny",
                )
    return None


def _step_command_deny(request: PermissionRequest, policy: Policy) -> PermissionDecision | None:
    """5. Command deny (applies even at the unrestricted tier)."""
    if request.command is not None:
        for pattern in (*BUILTIN_COMMAND_DENY, *policy.command_deny):
            if pattern.search(request.command) is not None:
                return PermissionDecision(
                    outcome=Outcome.DENY,
                    reason=f"command-deny matched {pattern.pattern!r}",
                    rule="command_deny",
                )
    return None


def _step_unrestricted_tier(
    request: PermissionRequest, policy: Policy
) -> PermissionDecision | None:
    """6. Unrestricted tier allows everything that survived the denies above."""
    if policy.tier is SandboxTier.UNRESTRICTED:
        return PermissionDecision(
            outcome=Outcome.ALLOW, reason="unrestricted tier", rule="tier_unrestricted"
        )
    return None


def _step_read_only(request: PermissionRequest, policy: Policy) -> PermissionDecision | None:
    """7. A read-only, non-network action is always allowed."""
    if request.is_read_only and request.network_host is None:
        return PermissionDecision(
            outcome=Outcome.ALLOW, reason="read-only action", rule="read_only"
        )
    return None


def _step_effectful(request: PermissionRequest, policy: Policy) -> PermissionDecision | None:
    """8. Effectful action: gate by session tier, then tool trust, then write boundary."""
    effects = _effects(request)
    for effect in effects:
        need = effect.required_tier
        if policy.tier < need:
            return PermissionDecision(
                outcome=Outcome.DENY,
                reason=f"session tier {policy.tier.name.lower()} forbids {effect.label}",
                rule="tier_session",
            )
        trusted = policy.required_tier.get(request.tool_name, SandboxTier.READ_ONLY)
        if trusted < need:
            return PermissionDecision(
                outcome=Outcome.ASK,
                reason=(
                    f"tool {request.tool_name!r} not trusted for {effect.label}; "
                    "promote in tool-tier-overrides"
                ),
                rule="tier_trust",
            )

    if Effect.WRITE in effects:
        for path in request.target_paths:
            ok, reason = validate_repo_write(path, policy.cwd, policy.extra_allowed)
            if not ok:
                return PermissionDecision(
                    outcome=Outcome.DENY, reason=reason, rule="path_boundary"
                )

    if effects:
        return PermissionDecision(
            outcome=Outcome.ALLOW, reason="permitted by tier", rule="tier_grant"
        )
    return None


# The fixed Spec 13A step order — credential guard first, default-ask last.
_PIPELINE = (
    _step_credential_guard,
    _step_tool_deny,
    _step_path_deny,
    _step_command_deny,
    _step_unrestricted_tier,
    _step_read_only,
    _step_effectful,
)


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
    """POSIX forms to match a deny glob against.

    Includes the lexical absolute form, a cwd-relative form (when in-tree), and
    the symlink-resolved form. The resolved form prevents a deny rule being
    dodged via an in-repo symlink whose own path doesn't match the glob but
    whose target does (parallel to the credential guard's resolved candidate).
    """
    canonical = canonical_path_forms(path, cwd)
    absolute = canonical.lexical
    cwd_abs = Path(os.path.normpath(cwd.as_posix()))
    forms = [absolute.as_posix()]
    if absolute == cwd_abs or absolute.is_relative_to(cwd_abs):
        forms.append(absolute.relative_to(cwd_abs).as_posix())
    if canonical.resolved is not None:
        resolved = canonical.resolved.as_posix()
        if resolved not in forms:
            forms.append(resolved)
    return tuple(forms)
