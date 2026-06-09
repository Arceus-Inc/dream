"""``python -m dream.repl cron run <kind>`` — OS-trigger entrypoint.

Designed for Windows Task Scheduler / ``crontab -e`` / systemd-timer: fire
one cron kind synchronously and exit with a meaningful status code so the
external scheduler can see success / failure.

This bypasses the REPL session entirely; it just needs the manifest dir
and the registry path. Bootstrap is run first so a brand-new repo (no
``.harness/cron/`` yet) works on the first invocation.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dream.config.paths import DreamPaths
from dream.services import cron as cron_service
from dream.tasks._cron import CRON_MANIFEST_DIR, load_cron_manifests
from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._types import TaskRecord

_TERMINAL_STATUSES = frozenset({"completed", "failed", "killed"})


def run_cron_cli(*, kind: str, working_dir: Path, timeout: int) -> int:
    """Fire ``kind`` once, wait up to ``timeout`` seconds, return an exit code.

    Exit codes:

    - ``0`` — cron session completed successfully (or was skipped because
      the manifest is disabled — a ``skipped`` run-record was written).
    - ``1`` — cron session ran but exited non-zero.
    - ``2`` — manifest not found for ``kind`` (operator misconfiguration).
    - ``3`` — cron session did not complete within ``timeout`` seconds.
    """
    paths = DreamPaths(repo=working_dir, home=Path.home()).ensure()
    registry_path = paths.dream_dir / "cron" / "registry.json"

    async def _run() -> int:
        manager = BackgroundTaskManager(tasks_dir=paths.tasks_dir)
        cron_service.bootstrap_default_manifests(working_dir)
        # Mirror REPL startup: seed the registry from the on-disk manifests so a
        # CLI-only setup keeps ``/cron list`` state and last_run/last_status in
        # sync, and so registry-level disables are honored by run_cron_kind.
        cron_service.ensure_registry_seeded(
            registry_path,
            load_cron_manifests(working_dir / CRON_MANIFEST_DIR),
        )

        done: asyncio.Event = asyncio.Event()
        result: dict[str, TaskRecord] = {}
        spawned_id: dict[str, str] = {}

        def _on_done(task: TaskRecord) -> None:
            tid = spawned_id.get("id")
            if tid is None or task.id != tid:
                return
            result["task"] = task
            done.set()

        unregister = manager.register_completion_listener(_on_done)
        try:
            record = await cron_service.run_cron_kind(
                kind=kind,
                working_dir=working_dir,
                manager=manager,
                registry_path=registry_path,
            )
            if record is None:
                # Manifest disabled — :func:`spawn_cron_session` already
                # wrote a ``skipped`` run-record. Nothing to wait for.
                sys.stdout.write(f"cron:{kind} skipped (manifest disabled)\n")
                return 0
            spawned_id["id"] = record.id
            # Close the race where a fast stub command reaches a terminal state
            # before ``spawned_id`` was set — its completion event would have
            # been ignored by ``_on_done``, so re-check terminal state here and
            # latch ``done`` directly rather than waiting for an event that
            # already fired.
            current = manager.get_task(record.id)
            if current is not None and current.status in _TERMINAL_STATUSES:
                result["task"] = current
                done.set()
            try:
                await asyncio.wait_for(done.wait(), timeout=timeout)
            except TimeoutError:
                sys.stderr.write(
                    f"cron:{kind} did not complete within {timeout}s\n"
                )
                return 3
        finally:
            unregister()

        finished = result.get("task")
        if finished is None:
            return 3
        exit_code = finished.return_code if finished.return_code is not None else 1
        sys.stdout.write(
            f"cron:{kind} finished status={finished.status} exit={exit_code}\n"
        )
        return 0 if exit_code == 0 else 1

    try:
        return asyncio.run(_run())
    except FileNotFoundError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


__all__ = ["run_cron_cli"]
