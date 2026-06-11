"""Backend selection for a session's sandbox posture (spec 13B).

The *tier* (read-only/repo-write/workspace-net/trusted) is enforced by
``dream.permissions`` at the gate; the *backend* is how approved
commands execute. v1 ships subprocess; docker is the gated seam.
"""

from __future__ import annotations

from dream.errors import SandboxError
from dream.sandbox._adapter import SandboxAdapter
from dream.sandbox.docker_backend import DockerSandbox
from dream.sandbox.subprocess_backend import SubprocessSandbox

__all__ = ["select_backend"]

def select_backend(name: str) -> SandboxAdapter:
    """Instantiate the named execution backend; refuse unknown names loudly."""
    if name == "subprocess":
        return SubprocessSandbox()
    if name == "docker":
        return DockerSandbox()
    raise SandboxError(
        f"unknown sandbox backend {name!r}; expected one of ['docker', 'subprocess']"
    )
