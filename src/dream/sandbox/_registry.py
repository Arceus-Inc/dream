"""Per-adapter Docker sandbox lifecycle (intentional vs OpenHarness global session).

Dream stamps one :class:`~dream.sandbox.SandboxAdapter` per harness into
``context_metadata``. Containers are owned by that adapter instance (lazy
start, atexit stop). A process-wide weak registry exists for debugging only —
it does not change ownership or auto-share containers across sessions.
"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dream.sandbox.docker_backend import DockerSandbox

_active: weakref.WeakSet[DockerSandbox] = weakref.WeakSet()


def register(sandbox: DockerSandbox) -> None:
    """Remember an active Docker sandbox instance (debug/introspection)."""
    _active.add(sandbox)


def active_sandboxes() -> list[DockerSandbox]:
    """Return currently reachable registered Docker sandboxes."""
    return list(_active)


__all__ = ["active_sandboxes", "register"]
