"""Sandbox — execution backends behind one adapter Protocol (spec 13B).

Tier *policy* lives in ``dream.permissions``; this package is the
*mechanism*: ``DockerSandbox`` (default) and ``SubprocessSandbox``
(opt-in) behind :class:`SandboxAdapter`.
"""

from __future__ import annotations

from dream.sandbox._adapter import SandboxAdapter, SandboxResult
from dream.sandbox._registry import active_sandboxes
from dream.sandbox._session import (
    SANDBOX_CONTEXT_KEY,
    read_sandbox_adapter,
    select_backend,
)
from dream.sandbox.docker_backend import (
    DockerAvailability,
    DockerSandbox,
    DockerSandboxConfig,
    get_docker_availability,
)
from dream.sandbox.subprocess_backend import SubprocessSandbox

__all__ = [
    "SANDBOX_CONTEXT_KEY",
    "DockerAvailability",
    "DockerSandbox",
    "DockerSandboxConfig",
    "SandboxAdapter",
    "SandboxResult",
    "SubprocessSandbox",
    "active_sandboxes",
    "get_docker_availability",
    "read_sandbox_adapter",
    "select_backend",
]
