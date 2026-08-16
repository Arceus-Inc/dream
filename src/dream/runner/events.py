"""Frozen observer events for ``run_task`` / ``run_role``.

Every progress boundary the runner emits is a typed, frozen dataclass.
Callers match on type — never on free-form dict payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from dream.engine._cost import UsageSnapshot
from dream.sprint._evaluation import EvaluationOutcome

__all__ = [
    "ContractWritten",
    "EvaluatorCompleted",
    "EvaluatorStarted",
    "GeneratorCompleted",
    "GeneratorStarted",
    "HeadRetry",
    "PlannerCompleted",
    "PlannerSkipped",
    "PlannerStarted",
    "RoleError",
    "RoleSessionClosed",
    "RoleSessionOpened",
    "RoleText",
    "RoleToolResult",
    "RoleToolStart",
    "RunTaskEvent",
    "RunTaskObserver",
    "SprintCompleted",
    "SprintEscalated",
    "SprintStarted",
    "TaskCompleted",
    "TaskStarted",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskStarted:
    task_id: str
    intent: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskCompleted:
    task_id: str
    sprint_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannerStarted:
    task_id: str
    intent: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannerCompleted:
    task_id: str
    spec_path: str
    ledger_path: str
    step_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannerSkipped:
    task_id: str
    reason: str
    ledger_path: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SprintStarted:
    sprint_number: int
    step_id: str
    step_description: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SprintCompleted:
    sprint_number: int
    step_id: str
    outcome: EvaluationOutcome | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SprintEscalated:
    task_id: str
    step_id: str
    needs_changes_count: int
    strikes_this_run: int
    sprint_number: int | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ContractWritten:
    sprint_number: int
    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratorStarted:
    sprint_number: int
    step_id: str
    has_contract: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratorCompleted:
    sprint_number: int
    step_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluatorStarted:
    sprint_number: int
    step_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluatorCompleted:
    sprint_number: int
    outcome: EvaluationOutcome
    score: float
    notes: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class HeadRetry:
    role: str
    attempt: int
    error: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleSessionOpened:
    role: str
    session_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleSessionClosed:
    role: str
    session_id: str
    model: str
    usage: UsageSnapshot
    cost_usd: float


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleText:
    role: str
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleToolStart:
    role: str
    tool: str
    input: Mapping[str, object]


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleToolResult:
    role: str
    tool: str
    is_error: bool
    content: str
    structured: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleError:
    role: str
    message: str


RunTaskEvent = (
    TaskStarted
    | TaskCompleted
    | PlannerStarted
    | PlannerCompleted
    | PlannerSkipped
    | SprintStarted
    | SprintCompleted
    | SprintEscalated
    | ContractWritten
    | GeneratorStarted
    | GeneratorCompleted
    | EvaluatorStarted
    | EvaluatorCompleted
    | HeadRetry
    | RoleSessionOpened
    | RoleSessionClosed
    | RoleText
    | RoleToolStart
    | RoleToolResult
    | RoleError
)


class RunTaskObserver(Protocol):
    """Called by the runner / role-session for every progress boundary.

    Implementations MUST be cheap and non-blocking — the runner calls
    ``on_event`` synchronously inside the hot path.
    """

    def on_event(self, event: RunTaskEvent) -> None: ...
