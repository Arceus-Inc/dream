"""Sprint contract artefact + path helpers.

Spec 10 §"Sprint contract":

- One JSON file per sprint at
  ``<worktree>/docs/exec-plans/active/{task-id}-sprint-{n}.json``.
- Written **before** the generator touches any source file in the worktree
  for that sprint (acceptance criterion #7).

Shapes only; :mod:`dream.sprint._plan_contract` assembles one from the ledger
step, and the runner (slice 10-G) writes it at the right time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dream.utils.fs import load_json_file, save_json_file

from ._checks import checked_sprint_number, checked_task_id

__all__ = [
    "VALID_VERIFICATION_KINDS",
    "SprintContract",
    "sprint_contract_path",
    "tech_debt_path",
]


VALID_VERIFICATION_KINDS: frozenset[str] = frozenset({"test", "lint", "eval"})


def _strict_bool(value: Any, *, field: str, default: bool) -> bool:
    """Parse a JSON value as a boolean without truthiness coercion.

    ``bool("false")`` is ``True``, so coercing externally-produced JSON would
    silently flip the flag. Accept only real booleans (and a missing key,
    which falls back to ``default``); reject everything else.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise TypeError(
        f"contract field {field!r} must be a boolean, got {type(value).__name__}"
    )


@dataclass(frozen=True)
class SprintContract:
    """The committed plan for one sprint — the bar the evaluator judges against."""

    task_id: str
    sprint_number: int
    goal: str
    acceptance_criteria: tuple[str, ...]
    verification_steps: tuple[dict[str, str], ...]
    scope_includes: tuple[str, ...] = ()
    scope_excludes: tuple[str, ...] = ()
    evaluator_enabled: bool = True
    rubric: str = ""

    def __post_init__(self) -> None:
        if not self.acceptance_criteria:
            raise ValueError("acceptance_criteria must contain at least one entry")
        for step in self.verification_steps:
            kind = step.get("kind")
            if kind not in VALID_VERIFICATION_KINDS:
                raise ValueError(
                    f"unknown verification step kind {kind!r}; "
                    f"expected one of {sorted(VALID_VERIFICATION_KINDS)}"
                )

    def to_dict(self) -> dict[str, Any]:
        # On-disk JSON shape:
        #   {"task_id": str, "sprint_number": int, "goal": str,
        #    "scope_includes": list[str], "scope_excludes": list[str],
        #    "acceptance_criteria": list[str],
        #    "verification_steps": list[{"kind": "test"|"lint"|"eval", ...}],
        #    "evaluator_enabled": bool, "rubric": str}
        return {
            "task_id": self.task_id,
            "sprint_number": self.sprint_number,
            "goal": self.goal,
            "scope_includes": list(self.scope_includes),
            "scope_excludes": list(self.scope_excludes),
            "acceptance_criteria": list(self.acceptance_criteria),
            "verification_steps": [dict(s) for s in self.verification_steps],
            "evaluator_enabled": self.evaluator_enabled,
            "rubric": self.rubric,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SprintContract:
        return cls(
            task_id=data["task_id"],
            sprint_number=int(data["sprint_number"]),
            goal=data["goal"],
            scope_includes=tuple(data.get("scope_includes", ())),
            scope_excludes=tuple(data.get("scope_excludes", ())),
            acceptance_criteria=tuple(data["acceptance_criteria"]),
            verification_steps=tuple(dict(s) for s in data.get("verification_steps", ())),
            evaluator_enabled=_strict_bool(
                data.get("evaluator_enabled"), field="evaluator_enabled", default=True
            ),
            rubric=str(data.get("rubric", "")),
        )

    def save(self, path: str | Path) -> None:
        save_json_file(path, self.to_dict())

    @classmethod
    def load(cls, path: str | Path) -> SprintContract:
        return cls.from_dict(load_json_file(path))


def sprint_contract_path(
    worktree_root: str | Path, *, task_id: str, sprint_number: int
) -> Path:
    """``<worktree>/docs/exec-plans/active/{task-id}-sprint-{n}.json``."""
    safe_id = checked_task_id(task_id)
    n = checked_sprint_number(sprint_number)
    return (
        Path(worktree_root)
        / "docs"
        / "exec-plans"
        / "active"
        / f"{safe_id}-sprint-{n}.json"
    )


def tech_debt_path(worktree_root: str | Path) -> Path:
    """``<worktree>/docs/exec-plans/tech-debt-tracker.md`` — append-only."""
    return Path(worktree_root) / "docs" / "exec-plans" / "tech-debt-tracker.md"
