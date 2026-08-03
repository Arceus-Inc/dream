"""Backend selection for a session's sandbox posture (spec 13B).

The *tier* (read-only/repo-write/workspace-net/trusted) is enforced by
``dream.permissions`` at the gate; the *backend* is how approved
commands execute. Docker is the default; subprocess is opt-in via
``backend = "subprocess"`` in ``.harness/sandbox.toml`` (never from the
tier alone).

The selected :class:`SandboxAdapter` rides the dispatcher's
``context_metadata`` under :data:`SANDBOX_CONTEXT_KEY` so the ``bash`` tool
executes through the one backend instead of spawning subprocesses itself,
mirroring the pattern :mod:`dream.tasks._session` uses for tasks.
"""

from __future__ import annotations

from dream.errors import SandboxError
from dream.sandbox._adapter import SandboxAdapter
from dream.sandbox.docker_backend import DockerSandbox, DockerSandboxConfig
from dream.sandbox.subprocess_backend import SubprocessSandbox

__all__ = ["SANDBOX_CONTEXT_KEY", "read_sandbox_adapter", "select_backend"]

SANDBOX_CONTEXT_KEY = "sandbox_adapter"


def read_sandbox_adapter(metadata: dict[str, object]) -> SandboxAdapter | None:
    """Return the :class:`SandboxAdapter` from tool ``metadata``, or ``None``.

    ``None`` is the bare-engine / older-caller path: tools fall back to their
    own execution mechanism so nothing breaks when no backend was wired.
    """
    value = metadata.get(SANDBOX_CONTEXT_KEY)
    return value if isinstance(value, SandboxAdapter) else None


def select_backend(
    name: str,
    *,
    docker: DockerSandboxConfig | None = None,
) -> SandboxAdapter:
    """Instantiate the named execution backend; refuse unknown names loudly."""
    if name == "subprocess":
        return SubprocessSandbox()
    if name == "docker":
        return DockerSandbox(config=docker or DockerSandboxConfig())
    raise SandboxError(
        f"unknown sandbox backend {name!r}; expected one of ['docker', 'subprocess']"
    )
