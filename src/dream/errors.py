"""Public exception hierarchy.

Every exception raised by the SDK is a `DreamError` subclass and carries
a stable string `code` consumers can branch on without parsing messages.
"""

from __future__ import annotations


class DreamError(Exception):
    """Base class for every SDK error. Carries a stable `code`."""

    code: str = "dream.error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class ProviderError(DreamError):
    """Upstream provider returned an error or malformed response."""

    code = "dream.provider"


class SandboxError(DreamError):
    """Sandbox could not execute the requested command."""

    code = "dream.sandbox"


class PermissionError(DreamError):
    """Permission check denied the operation."""

    code = "dream.permission"


class HookError(DreamError):
    """A hook raised, blocked without allow_block, or timed out."""

    code = "dream.hook"


class PluginError(DreamError):
    """Plugin loading, installation, or contribution failed."""

    code = "dream.plugin"


class CompactionError(DreamError):
    """History compaction failed or produced an invalid summary."""

    code = "dream.compaction"


__all__ = [
    "CompactionError",
    "DreamError",
    "HookError",
    "PermissionError",
    "PluginError",
    "ProviderError",
    "SandboxError",
]
