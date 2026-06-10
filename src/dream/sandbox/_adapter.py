"""Sandbox adapter Protocol + result shape (spec 13B).

One seam for "run this command inside the sandbox posture": the
subprocess backend implements it today; a container backend implements
the same Protocol later without touching call sites.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = ["SandboxAdapter", "SandboxResult"]


@dataclass(frozen=True)
class SandboxResult:
    """The outcome of one sandboxed command.

    ``returncode`` is ``None`` when the command produced no exit code
    (timeout kill or spawn failure).
    """

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@runtime_checkable
class SandboxAdapter(Protocol):
    """Executes one shell command under the active sandbox posture."""

    async def run(
        self,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 300.0,
    ) -> SandboxResult: ...
