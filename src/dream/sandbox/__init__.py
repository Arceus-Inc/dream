"""Sandbox — execution backends behind one adapter Protocol (spec 13B).

Tier *policy* lives in ``dream.permissions``; this package is the
*mechanism*: ``SubprocessSandbox`` (v1 default, repo as the boundary)
and ``DockerSandbox`` (always-refuse upgrade seam) behind
:class:`SandboxAdapter`.
"""

from __future__ import annotations

from dream.sandbox._adapter import SandboxAdapter, SandboxResult
from dream.sandbox._session import (
    SANDBOX_CONTEXT_KEY,
    read_sandbox_adapter,
    select_backend,
)
from dream.sandbox.docker_backend import DockerSandbox
from dream.sandbox.subprocess_backend import SubprocessSandbox

__all__ = [
    "SANDBOX_CONTEXT_KEY",
    "DockerSandbox",
    "SandboxAdapter",
    "SandboxResult",
    "SubprocessSandbox",
    "read_sandbox_adapter",
    "select_backend",
]
