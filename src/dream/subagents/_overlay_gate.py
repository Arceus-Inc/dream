"""Tighten-only permission overlay and worktree confinement wrappers."""

from __future__ import annotations

from pathlib import Path

from dream.engine._tool_dispatch import PermissionGate
from dream.permissions import Outcome, PermissionDecision, PermissionRequest
from dream.permissions._path_validator import validate_repo_write
from dream.subagents._overlay import EXECUTE_TOOLS, PermissionOverlay


def wrap_permission_gate(
    parent_gate: PermissionGate,
    overlay: PermissionOverlay,
) -> PermissionGate:
    """Return a gate that applies overlay denies, then consults ``parent_gate``.

    Overlay flags only deny. The parent decision is the sole allow path, so
    the child cannot widen past the parent.
    """
    if not overlay:
        return parent_gate

    def gate(request: PermissionRequest) -> PermissionDecision:
        if request.tool_name in overlay.tools:
            return PermissionDecision(
                outcome=Outcome.DENY,
                reason=f"subagent permission_overlay denies tool {request.tool_name!r}",
                rule="subagent_permission_overlay",
            )
        if overlay.write and (not request.is_read_only or _is_execute(request)):
            return PermissionDecision(
                outcome=Outcome.DENY,
                reason="subagent permission_overlay denies write effects",
                rule="subagent_permission_overlay",
            )
        if overlay.network and (request.network_host is not None or _is_execute(request)):
            return PermissionDecision(
                outcome=Outcome.DENY,
                reason="subagent permission_overlay denies network effects",
                rule="subagent_permission_overlay",
            )
        if overlay.execute and _is_execute(request):
            return PermissionDecision(
                outcome=Outcome.DENY,
                reason="subagent permission_overlay denies execute effects",
                rule="subagent_permission_overlay",
            )
        return parent_gate(request)

    return gate


def confine_permission_gate(parent_gate: PermissionGate, cwd: Path) -> PermissionGate:
    """Deny mutating paths that resolve outside ``cwd``.

    Used for ``IsolationMode.WORKTREE`` so the child cannot write the parent
    tree even when the parent policy lists extra-allowed roots.
    """
    root = cwd.resolve()

    def gate(request: PermissionRequest) -> PermissionDecision:
        if _is_execute(request):
            return PermissionDecision(
                outcome=Outcome.DENY,
                reason=(
                    "worktree isolation denies unconfinable command execution "
                    f"({request.tool_name!r})"
                ),
                rule="subagent_worktree_confine",
            )
        if not request.is_read_only:
            for path in request.target_paths:
                ok, reason = validate_repo_write(path, root)
                if not ok:
                    return PermissionDecision(
                        outcome=Outcome.DENY,
                        reason=reason,
                        rule="subagent_worktree_confine",
                    )
        return parent_gate(request)

    return gate


def _is_execute(request: PermissionRequest) -> bool:
    return request.tool_name in EXECUTE_TOOLS or request.command is not None


__all__ = ["confine_permission_gate", "wrap_permission_gate"]
