"""Confined filesystem adapter for harness working directories."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dream.contracts.tool import ToolResult
from dream.tools._paths import confine_path
from dream.tools.apply_patch._io import PatchFileOps
from dream.tools.apply_patch._scan import PatchPaths
from dream.tools.apply_patch._types import DiffError
from dream.utils.fs import atomic_write_text


@dataclass
class ConfinedPatchWorkspace:
    """Resolve and read/write/delete paths under a single working directory."""

    working_dir: Path
    _resolved: dict[str, Path] = field(default_factory=dict)

    def preflight(self, paths: PatchPaths) -> ToolResult | None:
        """Resolve every named path before any mutation; fail fast on escape."""
        for rel in paths.permission_targets:
            resolved = self._resolve(rel)
            if isinstance(resolved, ToolResult):
                return resolved
        return None

    def file_ops(self) -> PatchFileOps:
        return _WorkspaceOps(self)

    def _resolve(self, rel: str) -> Path | ToolResult:
        cached = self._resolved.get(rel)
        if cached is not None:
            return cached
        path = confine_path(self.working_dir, rel)
        if isinstance(path, ToolResult):
            return path
        self._resolved[rel] = path
        return path


@dataclass(frozen=True, slots=True)
class _WorkspaceOps:
    workspace: ConfinedPatchWorkspace

    def read(self, rel: str) -> str:
        path = self._path(rel)
        return path.read_text(encoding="utf-8")

    def write(self, rel: str, content: str) -> None:
        path = self._path(rel)
        if path.exists() and path.is_dir():
            raise DiffError(f"Cannot write to directory: {rel}")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)

    def delete(self, rel: str) -> None:
        path = self._path(rel)
        if path.exists() and path.is_file():
            path.unlink()

    def _path(self, rel: str) -> Path:
        resolved = self.workspace._resolve(rel)
        if isinstance(resolved, ToolResult):
            raise DiffError(f"path escapes working dir: {rel}")
        return resolved


__all__ = ["ConfinedPatchWorkspace", "atomic_write_text"]
