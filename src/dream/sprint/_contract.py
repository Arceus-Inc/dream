"""Sprint contract artefact + path helpers.

Spec 10 §"Sprint contract":

- One JSON file per sprint at
  ``<worktree>/docs/exec-plans/active/{task-id}-sprint-{n}.json``.
- Written **before** the generator touches any source file in the worktree
  for that sprint (acceptance criterion #7).
- ``negotiation_log`` is append-only and durable — including the disagreement
  that led to ``imposed: true``.

Shapes only; the orchestration that writes the contract at the right time
lives in :mod:`dream.sprint._negotiation` (which assembles it from the
negotiation result) and ultimately in the runner (slice 10-G).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from dream.utils.fs import atomic_write_text

from ._checks import checked_sprint_number, checked_task_id

__all__ = [
    "NegotiationEntry",
    "SprintContract",
    "VALID_VERIFICATION_KINDS",
    "sprint_contract_path",
    "tech_debt_path",
]


VALID_VERIFICATION_KINDS: frozenset[str] = frozenset({"test", "lint", "eval"})


@dataclass(frozen=True)
class NegotiationEntry:
    """One message in the contract negotiation log."""

    ts: str
    from_role: str
    to_role: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "from": self.from_role,
            "to": self.to_role,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NegotiationEntry:
        return cls(
            ts=data["ts"],
            from_role=data["from"],
            to_role=data["to"],
            message=data["message"],
        )


@dataclass(frozen=True)
class SprintContract:
    """The negotiated, committed plan for one sprint."""

    task_id: str
    sprint_number: int
    goal: str
    acceptance_criteria: tuple[str, ...]
    verification_steps: tuple[dict[str, str], ...]
    scope_includes: tuple[str, ...] = ()
    scope_excludes: tuple[str, ...] = ()
    evaluator_enabled: bool = True
    imposed: bool = False
    negotiation_log: tuple[NegotiationEntry, ...] = field(default_factory=tuple)

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

    def with_imposed(self, imposed: bool) -> SprintContract:
        return replace(self, imposed=imposed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "sprint_number": self.sprint_number,
            "goal": self.goal,
            "scope_includes": list(self.scope_includes),
            "scope_excludes": list(self.scope_excludes),
            "acceptance_criteria": list(self.acceptance_criteria),
            "verification_steps": [dict(s) for s in self.verification_steps],
            "evaluator_enabled": self.evaluator_enabled,
            "imposed": self.imposed,
            "negotiation_log": [e.to_dict() for e in self.negotiation_log],
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
            evaluator_enabled=bool(data.get("evaluator_enabled", True)),
            imposed=bool(data.get("imposed", False)),
            negotiation_log=tuple(
                NegotiationEntry.from_dict(e) for e in data.get("negotiation_log", ())
            ),
        )

    def save(self, path: str | Path) -> None:
        atomic_write_text(path, json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> SprintContract:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


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
