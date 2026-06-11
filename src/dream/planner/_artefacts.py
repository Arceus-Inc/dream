"""Planner artefacts (spec/ledger) and worktree path helpers.

Pinned by spec 10 §"Artefact shapes" + §"Task start (planner)":

- The planner's two committed outputs live under
  ``<worktree>/docs/exec-plans/active/{task-id}.md`` and ``.json``.
- The JSON ledger is the durable record of the plan's pending steps; the
  Markdown spec is the narrative the next role reads alongside it.

This module owns only the *shapes* and the *path math* — it does not
write files or emit events. See :mod:`dream.planner._run` for the
runs-once orchestration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from dream.utils.fs import atomic_write_text

__all__ = [
    "LedgerStep",
    "PlannerLedger",
    "StepStatus",
    "planner_ledger_path",
    "planner_spec_path",
]


StepStatus = Literal["pending", "in_progress", "done", "blocked"]
_VALID_STATUSES: frozenset[str] = frozenset({"pending", "in_progress", "done", "blocked"})


def _checked_task_id(task_id: str) -> str:
    if (
        not task_id
        or task_id in {".", ".."}
        or "/" in task_id
        or "\\" in task_id
        or "\x00" in task_id
        or Path(task_id).is_absolute()
    ):
        raise ValueError(f"unsafe task_id: {task_id!r}")
    return task_id


@dataclass(frozen=True)
class LedgerStep:
    """One pending unit of work the generator will pick up."""

    id: str
    description: str
    status: StepStatus = "pending"
    sprint_target: int | None = None
    notes: str = ""
    needs_changes_count: int = 0
    """How many times this step has received a ``needs-changes`` evaluation.

    Tracked so the runner can escalate to ``blocked`` after
    ``NEEDS_CHANGES_LIMIT`` consecutive rejections without burning the full
    sprint budget on a structurally impossible step.
    """

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(
                f"unknown status {self.status!r}; expected one of {sorted(_VALID_STATUSES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "notes": self.notes,
        }
        if self.sprint_target is not None:
            out["sprint_target"] = self.sprint_target
        # Omit when zero — follows sprint_target precedent; keeps existing
        # ledger JSON diffs minimal for steps that have never needed changes.
        if self.needs_changes_count:
            out["needs_changes_count"] = self.needs_changes_count
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerStep:
        return cls(
            id=data["id"],
            description=data["description"],
            status=data.get("status", "pending"),
            sprint_target=data.get("sprint_target"),
            notes=data.get("notes", ""),
            needs_changes_count=int(data.get("needs_changes_count", 0)),
        )


@dataclass(frozen=True)
class PlannerLedger:
    """The JSON ledger that ships beside the narrative spec.

    ``steps`` is an ordered tuple so equality and serialisation are stable.
    """

    task_id: str
    intent: str
    created_at: float
    steps: tuple[LedgerStep, ...] = field(default_factory=tuple)
    evaluator_enabled: bool = True
    version: int = 1

    def with_task_id(self, task_id: str) -> PlannerLedger:
        return replace(self, task_id=task_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "task_id": self.task_id,
            "intent": self.intent,
            "created_at": self.created_at,
            "evaluator_enabled": self.evaluator_enabled,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlannerLedger:
        return cls(
            task_id=data["task_id"],
            intent=data["intent"],
            created_at=float(data["created_at"]),
            steps=tuple(LedgerStep.from_dict(s) for s in data.get("steps", [])),
            evaluator_enabled=bool(data.get("evaluator_enabled", True)),
            version=int(data.get("version", 1)),
        )

    def save(self, path: str | Path) -> None:
        atomic_write_text(path, json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> PlannerLedger:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def planner_spec_path(worktree_root: str | Path, task_id: str) -> Path:
    return Path(worktree_root) / "docs" / "exec-plans" / "active" / f"{_checked_task_id(task_id)}.md"


def planner_ledger_path(worktree_root: str | Path, task_id: str) -> Path:
    return Path(worktree_root) / "docs" / "exec-plans" / "active" / f"{_checked_task_id(task_id)}.json"
