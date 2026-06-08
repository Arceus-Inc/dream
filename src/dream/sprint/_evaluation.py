"""Evaluation record artefact + write-once helpers.

Spec 10 acceptance criterion #10: when the evaluator runs, it writes
**exactly one** record per sprint under
``<worktree>/docs/evals/{task-id}/sprint-{n}.json``.

The shape is intentionally compatible with
:func:`dream.observability._events.evaluation_record_attrs` so a future
trace event can be derived from a saved record without re-parsing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dream.utils.fs import atomic_write_text

from ._checks import checked_sprint_number, checked_task_id

__all__ = [
    "EvaluationAlreadyRecorded",
    "EvaluationOutcome",
    "EvaluationRecord",
    "evaluation_record_path",
    "load_pending_carry_items",
    "record_evaluation",
]


EvaluationOutcome = Literal["pass", "needs-changes", "fail"]
_VALID_OUTCOMES: frozenset[str] = frozenset({"pass", "needs-changes", "fail"})


class EvaluationAlreadyRecorded(RuntimeError):
    """Raised by :func:`record_evaluation` when a record for this sprint exists.

    Criterion #10 — exactly one record per sprint when enabled."""


@dataclass(frozen=True)
class EvaluationRecord:
    """The evaluator's verdict for one sprint."""

    task_id: str
    sprint_number: int
    step_id: str
    outcome: EvaluationOutcome
    score: float = 0.0
    notes: str = ""
    items: tuple[str, ...] = field(default_factory=tuple)
    evaluator_version: str = "v0"

    def __post_init__(self) -> None:
        if self.outcome not in _VALID_OUTCOMES:
            raise ValueError(
                f"unknown outcome {self.outcome!r}; expected one of {sorted(_VALID_OUTCOMES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "sprint_number": self.sprint_number,
            "step_id": self.step_id,
            "outcome": self.outcome,
            "score": self.score,
            "notes": self.notes,
            "items": list(self.items),
            "evaluator_version": self.evaluator_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationRecord:
        return cls(
            task_id=data["task_id"],
            sprint_number=int(data["sprint_number"]),
            step_id=data["step_id"],
            outcome=data["outcome"],
            score=float(data.get("score", 0.0)),
            notes=data.get("notes", ""),
            items=tuple(data.get("items", ())),
            evaluator_version=data.get("evaluator_version", "v0"),
        )


def evaluation_record_path(
    worktree_root: str | Path, *, task_id: str, sprint_number: int
) -> Path:
    safe_id = checked_task_id(task_id)
    n = checked_sprint_number(sprint_number)
    return (
        Path(worktree_root) / "docs" / "evals" / safe_id / f"sprint-{n}.json"
    )


def record_evaluation(
    worktree_root: str | Path, record: EvaluationRecord
) -> Path:
    """Persist ``record`` and return its on-disk path.

    Refuses to overwrite an existing record for the same ``(task_id, sprint)``
    — criterion #10 ("exactly one evaluation record per sprint when enabled").
    """
    path = evaluation_record_path(
        worktree_root, task_id=record.task_id, sprint_number=record.sprint_number
    )
    if path.exists():
        raise EvaluationAlreadyRecorded(
            f"evaluation record already exists for {record.task_id}"
            f" sprint {record.sprint_number}: {path}"
        )
    atomic_write_text(path, json.dumps(record.to_dict(), indent=2) + "\n")
    return path


def load_pending_carry_items(
    worktree_root: str | Path, *, task_id: str, step_id: str
) -> tuple[str, ...]:
    """Return items the next sprint's negotiation MUST surface.

    Looks at the most-recent evaluation record for ``task_id`` and returns
    its ``items`` only when the outcome was ``needs-changes`` for the same
    ``step_id``. ``pass`` / ``fail`` outcomes don't carry forward.
    """
    safe_id = checked_task_id(task_id)
    evals_dir = Path(worktree_root) / "docs" / "evals" / safe_id
    if not evals_dir.is_dir():
        return ()
    records: list[tuple[int, EvaluationRecord]] = []
    for f in evals_dir.glob("sprint-*.json"):
        try:
            rec = EvaluationRecord.from_dict(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError):
            continue
        records.append((rec.sprint_number, rec))
    if not records:
        return ()
    records.sort(key=lambda pair: pair[0])
    _, latest = records[-1]
    if latest.outcome != "needs-changes" or latest.step_id != step_id:
        return ()
    return tuple(latest.items)
