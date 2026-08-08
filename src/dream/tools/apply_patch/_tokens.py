"""Codex apply_patch wire-format markers — single source of truth for line prefixes."""

from __future__ import annotations

from enum import StrEnum


class PatchMarker(StrEnum):
    """Line prefixes in the Codex multi-file patch format."""

    BEGIN = "*** Begin Patch"
    END = "*** End Patch"
    UPDATE = "*** Update File: "
    DELETE = "*** Delete File: "
    ADD = "*** Add File: "
    MOVE = "*** Move to: "
    EOF = "*** End of File"
    HUNK = "@@ "
    HUNK_BARE = "@@"


# Terminates the update-file hunk loop (hunk headers are consumed inside the loop).
_UPDATE_SECTION_END: tuple[str, ...] = (
    PatchMarker.END,
    "*** Update File:",
    "*** Delete File:",
    "*** Add File:",
    PatchMarker.EOF,
)

# Terminates a single hunk body while peeking diff lines.
_HUNK_LINE_END: tuple[str, ...] = (
    PatchMarker.HUNK_BARE,
    PatchMarker.END,
    "*** Update File:",
    "*** Delete File:",
    "*** Add File:",
    PatchMarker.EOF,
)

# Boundaries that terminate an add-file body.
_ADD_BOUNDARIES: tuple[str, ...] = (
    PatchMarker.END,
    "*** Update File:",
    "*** Delete File:",
    "*** Add File:",
)

__all__ = [
    "_ADD_BOUNDARIES",
    "_HUNK_LINE_END",
    "_UPDATE_SECTION_END",
    "PatchMarker",
]
