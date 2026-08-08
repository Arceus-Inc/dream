"""Filesystem abstraction injected by the tool layer."""

from __future__ import annotations

from typing import Protocol


class PatchFileOps(Protocol):
    """Minimal read/write/delete surface for applying a commit."""

    def read(self, path: str) -> str: ...
    def write(self, path: str, content: str) -> None: ...
    def delete(self, path: str) -> None: ...


__all__ = ["PatchFileOps"]
