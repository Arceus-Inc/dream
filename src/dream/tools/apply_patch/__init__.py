"""Codex ``apply_patch`` format — parse, validate, and apply multi-file patches.

Public surface for the engine (tests, execute_code stubs) and the Level-2 tool.
Wire-format markers live in :mod:`._tokens`; filesystem confinement in
:mod:`._workspace`.
"""

from __future__ import annotations

from dream.tools.apply_patch._engine import process_patch
from dream.tools.apply_patch._scan import (
    identify_files_added,
    identify_files_created,
    identify_files_moved,
    identify_files_needed,
    scan_patch_paths,
)
from dream.tools.apply_patch._tool import ApplyPatchInput, ApplyPatchTool
from dream.tools.apply_patch._types import (
    ActionType,
    Chunk,
    Commit,
    DiffError,
    FileChange,
    Patch,
    PatchAction,
    PatchResult,
)

__all__ = [
    "ActionType",
    "ApplyPatchInput",
    "ApplyPatchTool",
    "Chunk",
    "Commit",
    "DiffError",
    "FileChange",
    "Patch",
    "PatchAction",
    "PatchResult",
    "identify_files_added",
    "identify_files_created",
    "identify_files_moved",
    "identify_files_needed",
    "process_patch",
    "scan_patch_paths",
]
