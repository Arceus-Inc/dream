"""Public exception hierarchy.

Every exception raised by the SDK is a `DreamError` subclass and carries
a stable string `code` consumers can branch on without parsing messages.
"""

from __future__ import annotations

from typing import Literal

# The phase of ``run_task`` a fault surfaced in — the typed location a
# consumer (chorus) records on its escalation trail.
RunPhase = Literal["plan", "sprint", "evaluate"]
_RUN_PHASES: frozenset[str] = frozenset({"plan", "sprint", "evaluate"})


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


class TaskCancelled(DreamError):
    """A ``run_task`` was stopped cooperatively (caps/budget/operator).

    Distinct from a hard crash: the engine unwound its turn loop cleanly
    and checkpointed. The consumer releases the lock and leaves the task in
    its pre-beat state (spec 05 §5).
    """

    code = "dream.cancelled"


class RunTaskError(DreamError):
    """The ``run_task`` loop itself failed (planner/engine/tool fault).

    Carries the typed :attr:`phase` the fault surfaced in and the original
    ``cause``, so the consumer's escalation trail names *where* it broke
    (spec 05 §5). Not raised for a clean ``passed=False`` result (the DoD
    was simply not met) nor for a cooperative :class:`TaskCancelled`.
    """

    code = "dream.run_task"

    def __init__(
        self,
        message: str,
        *,
        phase: RunPhase,
        cause: BaseException | None = None,
        code: str | None = None,
    ) -> None:
        if phase not in _RUN_PHASES:
            raise ValueError(f"phase must be one of {sorted(_RUN_PHASES)}, got {phase!r}")
        super().__init__(message, code=code)
        self.phase: RunPhase = phase
        self.cause = cause


__all__ = [
    "CompactionError",
    "DreamError",
    "HookError",
    "PermissionError",
    "PluginError",
    "ProviderError",
    "RunTaskError",
    "SandboxError",
    "TaskCancelled",
]
