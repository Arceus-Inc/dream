"""Planner-runs-once orchestration shell.

Spec 10 acceptance criteria #1-#4 + the "Planner runs once and produces
both artefacts" gherkin scenario:

1. Planner runs exactly once per task — refusal via :class:`PlannerAlreadyRan`
   if either artefact already exists for the task id.
2. Produces both the markdown spec and the json ledger, atomically written
   under ``docs/exec-plans/active/`` in the worktree.
3. Restricted to read-only outside the exec-plan folder — the orchestrator
   writes exactly the two known files; the role manifest (slice 10-A)
   enforces tool-level read-only.
4. Emits ``planner.run.completed`` followed by ``handoff.planner_to_generator``
   carrying both file pointers (slice 10-B+C).

The actual LLM call is supplied by the caller as ``planner`` — slice 10-G
wires the production one through ``dream.repl session --role planner``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dream.planner._artefacts import (
    PlannerLedger,
    _checked_task_id,
    planner_ledger_path,
    planner_spec_path,
)
from dream.swarm._handoff import HandoffArtefact, handoff_event
from dream.utils.file_lock import exclusive_file_lock
from dream.utils.fs import atomic_write_text

__all__ = [
    "PlannerAlreadyRan",
    "PlannerCallable",
    "PlannerOutput",
    "PlannerResult",
    "run_planner",
]


@dataclass(frozen=True)
class PlannerOutput:
    """What a :data:`PlannerCallable` returns: the two artefacts to commit."""

    spec_markdown: str
    ledger: PlannerLedger


PlannerCallable = Callable[[str, str], Awaitable[PlannerOutput]]


@dataclass(frozen=True)
class PlannerResult:
    """The outcome of a successful planner run.

    ``events`` is the ordered jsonl payloads the runner should append to
    the session stream — :data:`planner.run.completed` first, then the
    handoff. The runner owns the actual sink.
    """

    task_id: str
    spec_path: Path
    ledger_path: Path
    events: tuple[dict[str, Any], ...]


class PlannerAlreadyRan(RuntimeError):
    """Raised when the planner-runs-once guard fires.

    A prior run for this task id left at least one of the two artefacts
    on disk; rerunning would overwrite it without an audit trail. The
    operator must either pick a new task id or archive the prior artefacts
    out of ``active/``.
    """


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


async def run_planner(
    *,
    task_id: str,
    intent: str,
    worktree_root: str | Path,
    planner: PlannerCallable,
) -> PlannerResult:
    spec_path = planner_spec_path(worktree_root, task_id)
    ledger_path = planner_ledger_path(worktree_root, task_id)

    await _write_artefacts(
        task_id=task_id,
        intent=intent,
        worktree_root=worktree_root,
        planner=planner,
        spec_path=spec_path,
        ledger_path=ledger_path,
    )

    root = Path(worktree_root)
    spec_rel = spec_path.relative_to(root).as_posix()
    ledger_rel = ledger_path.relative_to(root).as_posix()

    return PlannerResult(
        task_id=task_id,
        spec_path=spec_path,
        ledger_path=ledger_path,
        events=_build_events(task_id, spec_rel=spec_rel, ledger_rel=ledger_rel),
    )


async def _write_artefacts(
    *,
    task_id: str,
    intent: str,
    worktree_root: str | Path,
    planner: PlannerCallable,
    spec_path: Path,
    ledger_path: Path,
) -> None:
    """Run the planner once under a per-task lock and commit both artefacts.

    The lock makes "check runs-once + claim + write" atomic, closing the
    TOCTOU window a plain ``exists()``-then-write would leave open. The
    lockfile lives under ``.dream`` (not ``exec-plans/active``) so it never
    pollutes the artefact folder (criterion #3).
    """
    safe_id = _checked_task_id(task_id)
    lock_path = Path(worktree_root) / ".dream" / "planner" / f"{safe_id}.run.lock"
    with exclusive_file_lock(lock_path):
        if spec_path.exists() or ledger_path.exists():
            # Don't even invoke the LLM — the runs-once guard is a hard refusal.
            raise PlannerAlreadyRan(
                f"planner has already produced artefacts for task {task_id!r}: "
                f"spec={spec_path.exists()} ledger={ledger_path.exists()}"
            )

        output = await planner(task_id, intent)
        ledger = output.ledger.with_task_id(task_id)

        atomic_write_text(spec_path, output.spec_markdown)
        ledger.save(ledger_path)


def _build_events(
    task_id: str, *, spec_rel: str, ledger_rel: str
) -> tuple[dict[str, Any], ...]:
    """The ordered stream payloads: ``planner.run.completed`` then handoff."""
    completed = {
        "type": "planner.run.completed",
        "ts": _now_iso(),
        "task_id": task_id,
        "spec_path": spec_rel,
        "ledger_path": ledger_rel,
    }
    handoff = handoff_event(
        from_role="planner",
        to_role="generator",
        artefacts=[
            HandoffArtefact(kind="spec", path=spec_rel),
            HandoffArtefact(kind="ledger", path=ledger_rel),
        ],
    )
    return (completed, handoff)
