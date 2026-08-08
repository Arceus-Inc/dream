"""Typed patch AST and commit model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ActionType(StrEnum):
    ADD = "add"
    DELETE = "delete"
    UPDATE = "update"


class DiffError(ValueError):
    """Raised for invalid or unapplyable patch text."""


@dataclass(slots=True)
class Chunk:
    orig_index: int = -1
    del_lines: list[str] = field(default_factory=list)
    ins_lines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PatchAction:
    type: ActionType
    new_file: str | None = None
    chunks: list[Chunk] = field(default_factory=list)
    move_path: str | None = None


@dataclass(slots=True)
class Patch:
    actions: dict[str, PatchAction] = field(default_factory=dict)


@dataclass(slots=True)
class FileChange:
    type: ActionType
    old_content: str | None = None
    new_content: str | None = None
    move_path: str | None = None


@dataclass(slots=True)
class Commit:
    changes: dict[str, FileChange] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PatchResult:
    """Outcome of a successful parse + apply."""

    fuzz: int
    commit: Commit


__all__ = [
    "ActionType",
    "Chunk",
    "Commit",
    "DiffError",
    "FileChange",
    "Patch",
    "PatchAction",
    "PatchResult",
]
