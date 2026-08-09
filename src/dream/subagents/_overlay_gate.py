"""Tighten-only permission overlay applied to a child session gate."""

from __future__ import annotations

from dream.engine._tool_dispatch import PermissionGate
from dream.permissions import Outcome, PermissionDecision, PermissionRequest
from dream.subagents._declaration import PermissionDelta

# Capability tokens understood by the overlay (see dream.permissions PermissionEffect).
_WRITE_TOKENS: frozenset[str] = frozenset({"write", "repo-write", "repo-write+net-allowlist"})


def wrap_permission_gate(
    parent_gate: PermissionGate,
    overlay: PermissionDelta,
) -> PermissionGate:
    """Return a gate that denies overlay tokens, then consults ``parent_gate``.

    Overlay entries that look like tool names deny those tools. Entries in
    ``_WRITE_TOKENS`` deny any non-read-only request.
    """
    if not overlay:
        return parent_gate

    deny_tools = frozenset(token for token in overlay if token not in _WRITE_TOKENS)
    deny_writes = bool(_WRITE_TOKENS.intersection(overlay))

    def gate(request: PermissionRequest) -> PermissionDecision:
        if request.tool_name in deny_tools:
            return PermissionDecision(
                outcome=Outcome.DENY,
                reason=f"subagent permission_overlay denies tool {request.tool_name!r}",
                rule="subagent_permission_overlay",
            )
        if deny_writes and not request.is_read_only:
            return PermissionDecision(
                outcome=Outcome.DENY,
                reason="subagent permission_overlay denies write effects",
                rule="subagent_permission_overlay",
            )
        return parent_gate(request)

    return gate


__all__ = ["wrap_permission_gate"]
