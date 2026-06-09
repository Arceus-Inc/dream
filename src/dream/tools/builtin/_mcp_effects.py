"""Shared helper: derive a network host from an MCP server endpoint.

The permission gate (Spec 13C) gates network actions on ``network_host``. MCP
tools and ``mcp_auth`` both reach out to a server, so they surface the host of
the allowlisted endpoint here. ``stdio`` servers run a local subprocess and have
no network host, so they report ``None`` (no NETWORK effect to gate).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from dream.permissions import SandboxTier

# Tier an admitted MCP tool needs when its allowlist entry names no tier. MCP
# tools are external, so they sit above tier 0 (REPO_WRITE) by default until the
# operator narrows it on the allowlist entry.
_DEFAULT_MCP_TIER = SandboxTier.REPO_WRITE


def tier_required_for(wire: str) -> int:
    """Map an allowlist entry's tier *name* to the int a tool declaration needs.

    An empty/unset name means the operator did not narrow the entry, and an
    unrecognised name is a typo: both fall back to the conservative default
    (REPO_WRITE) rather than raising. ``from_wire("")`` already raises like any
    other unknown wire, so the single ``except`` covers both cases — admitting
    the tool at a known-safe tier is preferable to failing registration.
    """
    try:
        return int(SandboxTier.from_wire(wire))
    except ValueError:
        return int(_DEFAULT_MCP_TIER)


def endpoint_host(endpoint: str) -> str | None:
    """Return the host of an http/ws MCP endpoint, or ``None`` for stdio/local.

    ``stdio://`` endpoints address a local process, not a network peer, so they
    carry no host for the gate to screen. A malformed or hostless URL also
    yields ``None`` rather than raising — the caller treats a missing host as
    "no network effect", which is the conservative default for a read path.
    """
    parts = urlsplit(endpoint)
    if parts.scheme in ("stdio", ""):
        return None
    return parts.hostname or None


__all__ = ["endpoint_host", "tier_required_for"]
