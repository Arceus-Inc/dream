"""Orchestrate scan → parse → commit → apply."""

from __future__ import annotations

from contextlib import suppress

from dream.tools.apply_patch._commit import apply_commit, patch_to_commit
from dream.tools.apply_patch._io import PatchFileOps
from dream.tools.apply_patch._parser import parse_patch_text
from dream.tools.apply_patch._scan import scan_patch_paths
from dream.tools.apply_patch._tokens import PatchMarker
from dream.tools.apply_patch._types import DiffError, PatchResult


def process_patch(text: str, ops: PatchFileOps) -> PatchResult:
    """Parse ``text``, apply filesystem changes via ``ops``, return result metadata."""
    stripped = text.strip()
    if not stripped.startswith(PatchMarker.BEGIN):
        raise DiffError("Patch must start with *** Begin Patch")

    paths = scan_patch_paths(stripped)
    contents = _load_contents(paths.preload, ops)
    for rel in paths.destinations:
        with suppress(FileNotFoundError):
            contents[rel] = ops.read(rel)

    patch, fuzz = parse_patch_text(stripped, contents)
    commit = patch_to_commit(patch, contents)
    apply_commit(commit, ops)
    return PatchResult(fuzz=fuzz, commit=commit)


def _load_contents(paths: frozenset[str], ops: PatchFileOps) -> dict[str, str]:
    contents: dict[str, str] = {}
    for path in paths:
        try:
            contents[path] = ops.read(path)
        except FileNotFoundError as exc:
            raise DiffError(f"Missing File: {path}") from exc
    return contents


__all__ = ["process_patch"]
