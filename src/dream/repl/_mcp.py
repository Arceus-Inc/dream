"""REPL-side MCP session wiring (Spec 06 slice 4).

The setup logic now lives in :mod:`dream.mcp` so both the REPL loop and
``build_harness``'s async opener can drive it without the core depending on the
REPL. This module is a thin re-export kept for back-compatible imports; new
callers should import from :mod:`dream.mcp` directly.
"""

from __future__ import annotations

from dream.mcp._setup import (
    ALLOWLIST_RELPATH,
    CREDENTIALS_RELPATH,
    McpSetup,
    mcp_paths,
    setup_mcp_session,
)

__all__ = [
    "ALLOWLIST_RELPATH",
    "CREDENTIALS_RELPATH",
    "McpSetup",
    "mcp_paths",
    "setup_mcp_session",
]
