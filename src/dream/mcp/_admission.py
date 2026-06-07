"""MCP admission: reconcile declared config against the allowlist (Spec 06 #12).

Config (settings + plugins) declares what servers *could* connect; the allowlist
declares what *may*. A declared server absent from the allowlist is a blocking
finding — the session must not start. Returns the admitted entries (the
connectable set) plus the findings.
"""

from __future__ import annotations

from collections.abc import Iterable

from dream.mcp._types import AllowlistEntry
from dream.services.repo_validator import Finding


def admit(
    configured_names: Iterable[str],
    allowlist: list[AllowlistEntry],
) -> tuple[list[AllowlistEntry], list[Finding]]:
    """Return ``(admitted_entries, findings)``; unlisted declared servers block."""
    allowed = {entry.name for entry in allowlist}
    findings = [
        Finding(
            severity="blocking",
            code="mcp_not_on_allowlist",
            message=f"MCP server not on allowlist: {name}",
        )
        for name in sorted(set(configured_names))
        if name not in allowed
    ]
    return list(allowlist), findings


__all__ = ["admit"]
