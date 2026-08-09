"""Public exception hierarchy.

Every exception raised by the SDK is a `DreamError` subclass and carries
a stable string `code` consumers can branch on without parsing messages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from dream.services.session_store import SessionSnapshotRevision

# The phase of ``run_task`` a fault surfaced in — the typed location a
# consumer (chorus) records on its escalation trail.
RunPhase = Literal["plan", "sprint", "evaluate"]
_RUN_PHASES: frozenset[str] = frozenset({"plan", "sprint", "evaluate"})

# Why a saved session could not be resumed. A consumer holding a session
# handle branches on this to decide *retry fresh* vs *escalate*: every
# reason except ``working_dir_mismatch`` means the stored handle is spent
# and should be dropped before the next attempt.
SessionResumeFailure = Literal["missing", "corrupt", "schema_mismatch", "working_dir_mismatch"]
_SESSION_RESUME_FAILURES: frozenset[str] = frozenset(
    {"missing", "corrupt", "schema_mismatch", "working_dir_mismatch"}
)


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


class SessionResumeError(DreamError):
    """A saved session could not be restored.

    Carries the typed :attr:`reason` and the :attr:`session_id` that failed,
    so a control plane holding the handle can apply the recovery it wants:
    start a fresh session and drop the stored handle, or escalate. Only
    ``working_dir_mismatch`` describes a *reusable* snapshot — the session is
    intact, it just belongs to another working directory. When bytes were
    readable, :attr:`revision` identifies the exact snapshot that failed.
    """

    code = "dream.session_resume"

    def __init__(
        self,
        message: str,
        *,
        reason: SessionResumeFailure,
        session_id: str,
        revision: SessionSnapshotRevision | None = None,
        cause: BaseException | None = None,
        code: str | None = None,
    ) -> None:
        if reason not in _SESSION_RESUME_FAILURES:
            raise ValueError(
                f"reason must be one of {sorted(_SESSION_RESUME_FAILURES)}, got {reason!r}"
            )
        super().__init__(message, code=code)
        self.reason: SessionResumeFailure = reason
        self.session_id = session_id
        self.revision = revision
        self.cause = cause

    @property
    def should_clear_handle(self) -> bool:
        """Whether the caller's stored handle is spent and must be dropped."""
        return self.reason != "working_dir_mismatch"


class SessionSaveConflictError(DreamError):
    """A session save lost optimistic ownership of its snapshot path.

    ``expected_revision`` is the snapshot the session resumed, or ``None``
    when it opened against a missing path. ``actual_revision`` is what the
    store found while holding its inter-process lock.
    """

    code = "dream.session_save_conflict"

    def __init__(
        self,
        message: str,
        *,
        session_id: str,
        expected_revision: SessionSnapshotRevision | None,
        actual_revision: SessionSnapshotRevision | None,
        code: str | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.session_id = session_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


__all__ = [
    "CompactionError",
    "DreamError",
    "HookError",
    "PermissionError",
    "PluginError",
    "ProviderError",
    "RunTaskError",
    "SandboxError",
    "SessionResumeError",
    "SessionSaveConflictError",
    "TaskCancelled",
]
