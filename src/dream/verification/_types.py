"""Verification runtime types (Spec 12c — verify like a user).

A declared :class:`VerificationStepSpec` (one shell command) is executed into a
:class:`RepoVerificationStep` result; the run's results collect into a
:class:`VerificationReport`. Shapes are adapted from the OpenHarness
``RepoVerificationStep`` / ``RepoRunResult`` reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VerificationStatus = Literal["success", "failed", "skipped", "error"]

# Worst-of precedence for the overall report status. A skip is non-degrading
# (the spec: a skipped UI assertion is not a failure — fall back and proceed),
# so it ranks at the success floor; only a real failed/error step degrades.
_STATUS_RANK: dict[VerificationStatus, int] = {
    "success": 0,
    "skipped": 0,
    "failed": 1,
    "error": 2,
}


@dataclass(frozen=True)
class VerificationStepSpec:
    """A declared verification step: one shell command, optionally labelled."""

    command: str
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or self.command


@dataclass(frozen=True)
class RepoVerificationStep:
    """The result of running one step (OpenHarness ``RepoVerificationStep`` shape).

    ``returncode`` is ``None`` when the command never produced an exit code
    (``error`` — spawn failure or timeout — or ``skipped``).
    """

    command: str
    status: VerificationStatus
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    name: str = ""


@dataclass(frozen=True)
class VerificationReport:
    """The collected result of a verification run."""

    steps: tuple[RepoVerificationStep, ...] = ()

    @property
    def status(self) -> VerificationStatus:
        """Worst step status (error > failed > skipped > success); empty → success."""
        worst: VerificationStatus = "success"
        for step in self.steps:
            if _STATUS_RANK[step.status] > _STATUS_RANK[worst]:
                worst = step.status
        return worst

    @property
    def failures(self) -> tuple[RepoVerificationStep, ...]:
        return tuple(s for s in self.steps if s.status in ("failed", "error"))


__all__ = [
    "RepoVerificationStep",
    "VerificationReport",
    "VerificationStatus",
    "VerificationStepSpec",
]
