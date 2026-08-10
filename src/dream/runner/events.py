"""Frozen observer events for ``run_task`` / ``run_role``.

Every progress boundary the runner emits is a typed, frozen dataclass with a
stable ``kind`` discriminator. Callers match on type (or ``event.kind``) —
never on free-form dict payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

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
    kind: Literal["task.started"] = "task.started"
    task_id: str
    intent: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskCompleted:
    kind: Literal["task.completed"] = "task.completed"
    task_id: str
    sprint_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannerStarted:
    kind: Literal["planner.started"] = "planner.started"
    task_id: str
    intent: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannerCompleted:
    kind: Literal["planner.completed"] = "planner.completed"
    task_id: str
    spec_path: str
    ledger_path: str
    step_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannerSkipped:
    kind: Literal["planner.skipped"] = "planner.skipped"
    task_id: str
    reason: str
    ledger_path: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SprintStarted:
    kind: Literal["sprint.started"] = "sprint.started"
    sprint_number: int
    step_id: str
    step_description: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SprintCompleted:
    kind: Literal["sprint.completed"] = "sprint.completed"
    sprint_number: int
    step_id: str
    outcome: EvaluationOutcome | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SprintEscalated:
    kind: Literal["sprint.escalated"] = "sprint.escalated"
    task_id: str
    step_id: str
    needs_changes_count: int
    strikes_this_run: int
    sprint_number: int | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ContractWritten:
    kind: Literal["contract.written"] = "contract.written"
    sprint_number: int
    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratorStarted:
    kind: Literal["generator.started"] = "generator.started"
    sprint_number: int
    step_id: str
    has_contract: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratorCompleted:
    kind: Literal["generator.completed"] = "generator.completed"
    sprint_number: int
    step_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluatorStarted:
    kind: Literal["evaluator.started"] = "evaluator.started"
    sprint_number: int
    step_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluatorCompleted:
    kind: Literal["evaluator.completed"] = "evaluator.completed"
    sprint_number: int
    outcome: EvaluationOutcome
    score: float
    notes: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class HeadRetry:
    kind: Literal["head.retry"] = "head.retry"
    role: str
    attempt: int
    error: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleSessionOpened:
    kind: Literal["role.session.opened"] = "role.session.opened"
    role: str
    session_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleSessionClosed:
    kind: Literal["role.session.closed"] = "role.session.closed"
    role: str
    session_id: str
    model: str
    usage: UsageSnapshot
    cost_usd: float


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleText:
    kind: Literal["role.text"] = "role.text"
    role: str
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleToolStart:
    kind: Literal["role.tool.start"] = "role.tool.start"
    role: str
    tool: str
    input: Mapping[str, object]


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleToolResult:
    kind: Literal["role.tool.result"] = "role.tool.result"
    role: str
    tool: str
    is_error: bool
    content: str
    content_preview: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RoleError:
    kind: Literal["role.error"] = "role.error"
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


@runtime_checkable
class RunTaskObserver(Protocol):
    """Called by the runner / role-session for every progress boundary.

    Implementations MUST be cheap and non-blocking — the runner calls
    ``on_event`` synchronously inside the hot path.
    """

    def on_event(self, event: RunTaskEvent) -> None: ...
