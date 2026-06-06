"""Spec 07 slice 3 — cron-as-session glue.

A cron *kind* runs as a normal harness session: the runner allocates a
``task_id``, creates a worktree (Spec 02), and spawns a ``local_agent``
:class:`TaskRecord` with the manifest's ``entry_prompt`` as the intent.
This module supplies the small piece that ties the runtime layer
(:class:`BackgroundTaskManager`) to the durable observability layer:

- :class:`CronRunRecord` — the JSON artefact written to
  ``docs/cron-runs/{kind}/{YYYY-MM-DD}-{run-id}.json`` after every run
  (Spec 07 §"Cron run-record").
- :func:`make_cron_run_listener` — a completion listener that derives the
  run outcome from the terminal task and commits the record.
- :func:`spawn_cron_session` — the entrypoint the runner calls. Records
  ``cron.skipped`` when the manifest is disabled and otherwise spawns a
  ``local_agent`` task wired to the listener.
- :data:`MAX_SESSION_MINUTES_METADATA_KEY` — the metadata key that signals
  ``max_session_minutes`` overrun so the run-record carries
  ``outcome: failed`` with the standard failure reason (Spec 07 MUST 25).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from dream.tasks._cron import CronManifest
from dream.tasks._manager import BackgroundTaskManager, CompletionListener
from dream.tasks._types import TaskRecord
from dream.utils.fs import atomic_write_text

__all__ = [
    "CRON_RUNS_ROOT",
    "MAX_SESSION_MINUTES_METADATA_KEY",
    "CronRunOutcome",
    "CronRunRecord",
    "cron_run_record_path",
    "make_cron_run_listener",
    "read_cron_run_records",
    "spawn_cron_session",
    "write_cron_run_record",
]


CRON_RUNS_ROOT = "docs/cron-runs"
"""Repo-relative default root for cron run records (Spec 07 §Artefact shapes)."""

MAX_SESSION_MINUTES_METADATA_KEY = "cron.max_session_minutes_exceeded"
"""Marker key the runner sets on the task metadata when the cron-level
``max_session_minutes`` cap aborts the session via the turn-timeout
machinery (Spec 03). The completion listener treats this as the
``max-session-minutes`` failure reason on the run-record."""


CronRunOutcome = Literal["success", "no-op", "failed", "skipped"]


# ---------------------------------------------------------------------------
# CronRunRecord
# ---------------------------------------------------------------------------


class CronRunRecord(BaseModel):
    """One cron run's persisted observability record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    run_id: str
    started_at: datetime
    ended_at: datetime | None = None
    outcome: CronRunOutcome
    prs_opened: tuple[str, ...] = ()
    session_jsonl: str | None = None
    failure_reason: str | None = None


def cron_run_record_path(
    runs_root: str | Path,
    *,
    kind: str,
    run_id: str,
    started_at: datetime,
) -> Path:
    """Compute the path for one run's record: ``{runs_root}/{kind}/{YYYY-MM-DD}-{run_id}.json``."""
    date = started_at.astimezone(UTC).strftime("%Y-%m-%d")
    return Path(runs_root) / kind / f"{date}-{run_id}.json"


def write_cron_run_record(
    runs_root: str | Path, record: CronRunRecord
) -> Path:
    """Persist ``record`` under :func:`cron_run_record_path` (atomic)."""
    target = cron_run_record_path(
        runs_root,
        kind=record.kind,
        run_id=record.run_id,
        started_at=record.started_at,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, record.model_dump_json(indent=2) + "\n")
    return target


def read_cron_run_records(runs_root: str | Path, kind: str) -> list[CronRunRecord]:
    """Read every persisted record for ``kind`` in chronological order."""
    folder = Path(runs_root) / kind
    if not folder.is_dir():
        return []
    out: list[CronRunRecord] = []
    for path in sorted(folder.glob("*.json")):
        out.append(CronRunRecord.model_validate_json(path.read_text(encoding="utf-8")))
    return out


# ---------------------------------------------------------------------------
# completion listener
# ---------------------------------------------------------------------------


def _derive_outcome(task: TaskRecord) -> tuple[CronRunOutcome, str | None]:
    if task.metadata.get(MAX_SESSION_MINUTES_METADATA_KEY):
        return "failed", "max-session-minutes"
    if task.status == "completed" and task.return_code == 0:
        return "success", None
    if task.status == "killed":
        return "failed", "killed"
    rc = task.return_code if task.return_code is not None else "?"
    return "failed", f"return_code={rc}"


def _split_prs(metadata: dict[str, str]) -> tuple[str, ...]:
    raw = metadata.get("cron.prs_opened", "")
    if not raw:
        return ()
    return tuple(p for p in (s.strip() for s in raw.split(",")) if p)


def make_cron_run_listener(
    *,
    runs_root: str | Path,
    kind: str,
    run_id: str,
    started_at: datetime,
) -> CompletionListener:
    """Build a completion listener that writes the cron run-record."""

    def listener(task: TaskRecord) -> None:
        outcome, failure = _derive_outcome(task)
        ended = (
            datetime.fromtimestamp(task.ended_at, UTC) if task.ended_at else datetime.now(UTC)
        )
        record = CronRunRecord(
            kind=kind,
            run_id=run_id,
            started_at=started_at,
            ended_at=ended,
            outcome=outcome,
            prs_opened=_split_prs(task.metadata),
            session_jsonl=task.metadata.get("cron.session_jsonl") or None,
            failure_reason=failure,
        )
        write_cron_run_record(runs_root, record)

    return listener


# ---------------------------------------------------------------------------
# spawn_cron_session
# ---------------------------------------------------------------------------


def _new_run_id() -> str:
    return uuid4().hex[:12]


async def spawn_cron_session(
    *,
    manager: BackgroundTaskManager,
    manifest: CronManifest,
    cwd: str | Path,
    runs_root: str | Path,
    command: str | None = None,
    argv: list[str] | None = None,
    run_id: str | None = None,
    started_at: datetime | None = None,
    extra_metadata: dict[str, str] | None = None,
) -> TaskRecord | None:
    """Spawn a cron-as-session task, or record ``cron.skipped`` when
    ``manifest.enabled=False``.

    Returns the spawned :class:`TaskRecord` on success, or ``None`` when
    the manifest is disabled. When disabled, a ``skipped`` run-record is
    written to ``runs_root`` so the no-run is still observable
    (Spec 07 MUST 21).

    Exactly one of ``command`` / ``argv`` must be supplied (forwarded to
    :meth:`BackgroundTaskManager.create_shell_task`). The runner uses
    this to invoke ``harness session --intent {entry_prompt} ...`` or
    equivalent — the shape is not pinned here.
    """
    rid = run_id or _new_run_id()
    start = started_at or datetime.now(UTC)

    if not manifest.enabled:
        skipped = CronRunRecord(
            kind=manifest.name,
            run_id=rid,
            started_at=start,
            ended_at=start,
            outcome="skipped",
            failure_reason="disabled",
        )
        write_cron_run_record(runs_root, skipped)
        return None

    metadata: dict[str, str] = {
        "cron.kind": manifest.name,
        "cron.run_id": rid,
        "cron.runs_root": str(runs_root),
    }
    if manifest.tier_required:
        metadata["cron.tier_required"] = manifest.tier_required
    if manifest.max_session_minutes is not None:
        metadata["cron.max_session_minutes"] = str(manifest.max_session_minutes)
    if extra_metadata:
        metadata.update(extra_metadata)

    base_listener = make_cron_run_listener(
        runs_root=runs_root, kind=manifest.name, run_id=rid, started_at=start
    )
    # Self-unregister after the first terminal transition for this run —
    # avoids leaking listeners across many cron ticks.
    spawned_task_id: dict[str, str] = {}
    unregister: dict[str, object] = {}

    def one_shot(task: TaskRecord) -> None:
        if spawned_task_id and task.id != spawned_task_id["id"]:
            return
        try:
            base_listener(task)
        finally:
            unreg = unregister.get("fn")
            if callable(unreg):
                unreg()

    unregister["fn"] = manager.register_completion_listener(one_shot)

    record = await manager.create_shell_task(
        description=f"cron:{manifest.name}",
        cwd=cwd,
        command=command,
        argv=argv,
        task_type="local_agent",
        metadata=metadata,
    )
    spawned_task_id["id"] = record.id
    return record
