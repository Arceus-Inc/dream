"""Discover paths referenced in patch text without parsing hunks."""

from __future__ import annotations

from dataclasses import dataclass

from dream.tools.apply_patch._tokens import PatchMarker


@dataclass(frozen=True, slots=True)
class PatchPaths:
    """All repository-relative paths named in a patch body."""

    sources: frozenset[str]
    creates: frozenset[str]
    moves: frozenset[str]

    @property
    def preload(self) -> frozenset[str]:
        """Paths that must exist on disk before parsing (update/delete sources)."""
        return self.sources

    @property
    def destinations(self) -> frozenset[str]:
        """New paths that may be created (add + move targets)."""
        return self.creates | self.moves

    @property
    def permission_targets(self) -> frozenset[str]:
        """Every path the patch may touch — for effects_for / preflight."""
        return self.sources | self.creates | self.moves


def scan_patch_paths(text: str) -> PatchPaths:
    """Single-pass scan of patch lines for path references."""
    sources: set[str] = set()
    creates: set[str] = set()
    moves: set[str] = set()
    for line in text.strip().splitlines():
        if line.startswith(PatchMarker.UPDATE):
            sources.add(line.removeprefix(PatchMarker.UPDATE))
        elif line.startswith(PatchMarker.DELETE):
            sources.add(line.removeprefix(PatchMarker.DELETE))
        elif line.startswith(PatchMarker.ADD):
            creates.add(line.removeprefix(PatchMarker.ADD))
        elif line.startswith(PatchMarker.MOVE):
            moves.add(line.removeprefix(PatchMarker.MOVE))
    return PatchPaths(
        sources=frozenset(sources),
        creates=frozenset(creates),
        moves=frozenset(moves),
    )


# Back-compat aliases for callers that used the old free functions.
def identify_files_needed(text: str) -> list[str]:
    return list(scan_patch_paths(text).sources)


def identify_files_added(text: str) -> list[str]:
    return list(scan_patch_paths(text).creates)


def identify_files_moved(text: str) -> list[str]:
    return list(scan_patch_paths(text).moves)


def identify_files_created(text: str) -> list[str]:
    paths = scan_patch_paths(text)
    return list(paths.creates | paths.moves)


__all__ = [
    "PatchPaths",
    "identify_files_added",
    "identify_files_created",
    "identify_files_moved",
    "identify_files_needed",
    "scan_patch_paths",
]
