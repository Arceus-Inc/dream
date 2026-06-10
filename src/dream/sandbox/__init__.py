"""Sandbox — execution backends behind one adapter Protocol (spec 13B).

Tier *policy* lives in ``dream.permissions``; this package is the
*mechanism*: ``SubprocessSandbox`` (v1 default, repo as the boundary)
and ``DockerSandbox`` (always-refuse upgrade seam) behind
:class:`SandboxAdapter`.
"""

from __future__ import annotations

from dream.sandbox._adapter import SandboxAdapter, SandboxResult
from dream.sandbox._session import select_backend
from dream.sandbox.docker_backend import DockerSandbox
from dream.sandbox.subprocess_backend import SubprocessSandbox

__all__ = [
    "DockerSandbox",
    "SandboxAdapter",
    "SandboxResult",
    "SubprocessSandbox",
    "select_backend",
]
