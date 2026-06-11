"""Docker sandbox backend — the gated upgrade seam (spec 13B).

Docker is an upgrade path, not a dependency: the seam exists so the
adapter surface is proven, but it refuses every call until a real
container backend lands (same posture as ``swarm/_remote``). Needed for
the compiled-deps SWE-bench tier and Terminal-Bench.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from dream.errors import SandboxError
from dream.sandbox._adapter import SandboxResult

__all__ = ["DockerSandbox"]


class DockerSandbox:
    """Always-refuse placeholder for the container backend."""

    async def run(
        self,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 300.0,
    ) -> SandboxResult:
        raise SandboxError(
            "docker sandbox backend is not implemented in v1; "
            "use the subprocess backend or contribute the container adapter"
        )
