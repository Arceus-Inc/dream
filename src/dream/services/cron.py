"""croniter-driven scheduler with file-locked registry (Spec 07 trigger surface).

Spec 07 ships the cron *data layer* in :mod:`dream.tasks._cron` and the
cron-as-session *spawn shape* in :mod:`dream.tasks._cron_session`. This
module is the *trigger* — what reads the registry, decides a job is due,
fires :func:`spawn_cron_session`, and rolls ``next_run`` forward via
:func:`mark_job_run`.

Three entrypoints:

- :func:`bootstrap_default_manifests` — write any missing
  ``.harness/cron/{kind}.toml`` from :data:`DEFAULT_CRON_KINDS`. Idempotent;
  existing files are left alone so operators can edit defaults in place.

- :func:`run_cron_kind` — fire one kind now. Used by both the in-REPL tick
  loop and the ``python -m dream.repl cron run <kind>`` operator CLI.

- :func:`cron_tick_loop` — long-running coroutine that polls every
  ``poll_seconds`` (default 30) and fires every enabled job whose
  ``next_run <= now``. Cancellation-safe; per-job exceptions are logged
  and swallowed so a broken manifest can't kill the scheduler.

The spawned command shape is a deliberate stub today (``python -c
"print(...)"``) — the cron firing is *observable* in the REPL (▸ cron:<kind>
/ ↳ completion) and writes a ``docs/cron-runs/{kind}/...`` record, but the
session that runs inside is empty. Spec 10 (orchestration / role machinery)
and spec 11 (autopilot pipeline) replace :func:`_default_cron_argv` with a
real ``harness session --intent {entry_prompt}`` invocation.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from dream.tasks._cron import (
    CRON_MANIFEST_DIR,
    CronJob,
    CronManifest,
    default_cron_manifests,
    get_cron_job,
    load_cron_jobs,
    load_cron_manifest,
    mark_job_run,
    upsert_cron_job,
)
from dream.tasks._cron_session import CRON_RUNS_ROOT, spawn_cron_session
from dream.tasks._manager import (
    BackgroundTaskManager,
    register_one_shot_completion,
)
from dream.tasks._types import TaskRecord
from dream.utils.fs import atomic_write_text

__all__ = [
    "DEFAULT_POLL_SECONDS",
    "bootstrap_default_manifests",
    "cron_tick_loop",
    "ensure_registry_seeded",
    "run_cron_kind",
]

DEFAULT_POLL_SECONDS = 30
"""Tick interval for :func:`cron_tick_loop`. 30s is small enough to fire
minute-granular cron expressions on time, large enough that the registry
file lock isn't being grabbed constantly."""

def _record_completion_outcome(
    *,
    manager: BackgroundTaskManager,
    registry_path: str | Path,
    name: str,
    task_id: str,
) -> None:
    """Stamp the registry row from the task's *actual* terminal outcome.

    Spawning only confirms the session started; ``last_status`` / ``last_run``
    must reflect how it finished. A one-shot completion listener (keyed to the
    spawned task) updates the row via :func:`mark_job_run`, then unregisters
    itself so the listener set doesn't grow across ticks.
    """

    def _stamp(task: TaskRecord) -> None:
        success = task.status == "completed" and task.return_code == 0
        mark_job_run(registry_path, name, success=success)

    register_one_shot_completion(manager, task_id, _stamp)


def _manifest_dir(working_dir: Path) -> Path:
    return Path(working_dir) / CRON_MANIFEST_DIR


def _runs_root(working_dir: Path) -> Path:
    return Path(working_dir) / CRON_RUNS_ROOT


def _default_cron_argv(manifest: CronManifest) -> list[str]:
    """Stub spawn shape — visible firing without a real harness session.

    Uses :data:`sys.executable` so the spawn works in any environment
    (Windows fresh shell with no ``python`` on PATH, isolated venvs, etc.).
    Replaced by spec 10 / 11 with ``["harness", "session", "--intent",
    manifest.entry_prompt, ...]`` once the role machinery lands.
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")
    msg = f"cron:{manifest.name} fired at {now}"
    return [sys.executable, "-c", f"print({msg!r})"]


def _manifest_to_toml(manifest: CronManifest) -> str:
    """Render a :class:`CronManifest` as TOML.

    Hand-rolled rather than pulling in ``tomli_w`` because the manifest
    schema is narrow (str/bool/int scalars only) and we control every
    field name (no spec-character escaping needed beyond ``"``).
    """
    lines: list[str] = []
    for key, value in manifest.model_dump(exclude_none=True).items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        else:
            text = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{text}"')
    return "\n".join(lines) + "\n"


def bootstrap_default_manifests(working_dir: str | Path) -> list[Path]:
    """Write any missing default ``.harness/cron/{kind}.toml`` files.

    Returns the paths newly written this call (empty list if all four
    defaults already exist). Idempotent so safe to call on every REPL
    start.
    """
    out: list[Path] = []
    target_dir = _manifest_dir(Path(working_dir))
    target_dir.mkdir(parents=True, exist_ok=True)
    for manifest in default_cron_manifests():
        path = target_dir / f"{manifest.name}.toml"
        if path.exists():
            continue
        atomic_write_text(path, _manifest_to_toml(manifest))
        out.append(path)
    return out


def ensure_registry_seeded(
    registry_path: str | Path,
    manifests: Iterable[CronManifest],
) -> list[CronJob]:
    """Upsert any manifest whose ``name`` is not already in the registry.

    Existing registry entries are left alone — operators may have toggled
    ``enabled`` or edited the schedule via ``cron_toggle`` / ``cron_create``
    and we don't want bootstrap to clobber that on the next startup.
    """
    existing = {j.name for j in load_cron_jobs(registry_path)}
    added: list[CronJob] = []
    for manifest in manifests:
        if manifest.name in existing:
            continue
        added.append(upsert_cron_job(registry_path, manifest.to_job()))
    return added


async def run_cron_kind(
    *,
    kind: str,
    working_dir: str | Path,
    manager: BackgroundTaskManager,
    registry_path: str | Path | None = None,
) -> TaskRecord | None:
    """Fire a single cron kind now.

    Loads the manifest from ``.harness/cron/{kind}.toml`` (raises
    :class:`FileNotFoundError` if absent), spawns the cron session, and —
    when ``registry_path`` is provided and the job exists in the registry —
    advances ``next_run`` via :func:`mark_job_run`.

    Returns the spawned :class:`TaskRecord`, or ``None`` if the manifest
    is disabled (in which case a ``skipped`` run-record is written by
    :func:`spawn_cron_session`).
    """
    wd = Path(working_dir)
    manifest_path = _manifest_dir(wd) / f"{kind}.toml"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"no cron manifest for kind {kind!r} at {manifest_path}"
        )
    manifest = load_cron_manifest(manifest_path)
    registry = (
        Path(registry_path)
        if registry_path is not None and Path(registry_path).exists()
        else None
    )
    job = get_cron_job(registry, kind) if registry is not None else None
    # A registry-level disable (e.g. ``/cron toggle``) overrides an enabled
    # manifest: surface it as a ``skipped`` run-record by spawning with a
    # disabled manifest copy, mirroring the manifest-disabled path.
    effective = (
        manifest.model_copy(update={"enabled": False})
        if job is not None and not job.enabled
        else manifest
    )
    record = await spawn_cron_session(
        manager=manager,
        manifest=effective,
        cwd=wd,
        runs_root=_runs_root(wd),
        argv=_default_cron_argv(effective),
    )
    if registry is not None and job is not None and effective.enabled and record is not None:
        # Defer the registry stamp to the task's real terminal outcome instead
        # of optimistically recording success right after spawn.
        _record_completion_outcome(
            manager=manager,
            registry_path=registry,
            name=kind,
            task_id=record.id,
        )
    return record


async def cron_tick_loop(
    *,
    manager: BackgroundTaskManager,
    working_dir: str | Path,
    registry_path: str | Path,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
    argv_for: Callable[[CronManifest], list[str]] = _default_cron_argv,
    note_sink: Callable[[CronManifest], None] | None = None,
) -> None:
    """Long-running coroutine — poll registry, fire due jobs, sleep.

    ``argv_for`` maps a due manifest to the command the spawned task runs;
    the default is the visible-firing print stub. Consumer daemons supply
    their real payload (e.g. a one-shot digest run) here.

    ``note_sink`` receives manifests whose ``target = "next-wake"`` — the
    timed-note pattern: the firing queues a note for the wake scheduler
    instead of spawning. Without a sink (standalone loops, the REPL) such
    manifests fall back to the spawn path so firings are never dropped.

    Cancellation-safe: ``asyncio.CancelledError`` propagates out cleanly
    so ``task.cancel(); await task`` shapes shut the loop down without
    leaking partial firings. Per-job failures are recorded as
    ``last_status="failed"`` on the registry row (so operators see them
    via ``cron_list``) and the loop continues with the next job; a
    permanently-broken manifest cannot busy-loop the scheduler because
    ``mark_job_run`` rolls ``next_run`` forward on failure too.
    """
    wd = Path(working_dir)
    runs_root = _runs_root(wd)
    while True:
        try:
            now = datetime.now(UTC)
            for job in load_cron_jobs(registry_path):
                if not job.enabled:
                    continue
                if job.next_run is None or job.next_run > now:
                    continue
                manifest_path = _manifest_dir(wd) / f"{job.name}.toml"
                if not manifest_path.exists():
                    # Registry row without a matching manifest — roll
                    # next_run forward as a failed run so the scheduler
                    # doesn't grab the same stale row every tick.
                    mark_job_run(registry_path, job.name, success=False)
                    continue
                try:
                    manifest = load_cron_manifest(manifest_path)
                    if manifest.target == "next-wake" and note_sink is not None:
                        note_sink(manifest)
                        mark_job_run(registry_path, job.name, success=True)
                        continue
                    record = await spawn_cron_session(
                        manager=manager,
                        manifest=manifest,
                        cwd=wd,
                        runs_root=runs_root,
                        argv=argv_for(manifest),
                    )
                    # Roll next_run forward now so this job isn't re-grabbed on
                    # the next tick before it finishes (dedup); the final
                    # last_status is then corrected from the real terminal
                    # outcome instead of this optimistic stamp.
                    mark_job_run(registry_path, job.name, success=True)
                    if record is not None:
                        _record_completion_outcome(
                            manager=manager,
                            registry_path=registry_path,
                            name=job.name,
                            task_id=record.id,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    mark_job_run(registry_path, job.name, success=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Outer-loop fault (e.g. registry corruption). Print to stderr
            # so the operator running the REPL sees *something*; loop
            # continues because killing the scheduler silently is worse.
            sys.stderr.write(
                "cron scheduler tick raised; continuing\n"
            )
        await asyncio.sleep(poll_seconds)
