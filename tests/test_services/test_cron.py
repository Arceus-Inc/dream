"""Spec 07 trigger surface — scheduler bootstrap + tick loop + per-kind run.

Covers :mod:`dream.services.cron`:

- :func:`bootstrap_default_manifests` writes the four spec-mandated
  ``.harness/cron/*.toml`` files exactly once and never clobbers operator
  edits.
- :func:`ensure_registry_seeded` upserts manifests not already present in
  the registry and leaves existing rows alone.
- :func:`run_cron_kind` fires a kind once, writes a run-record, and
  advances ``next_run`` on the registry row.
- :func:`cron_tick_loop` polls the registry, fires due jobs, rolls
  ``next_run`` forward, and is cancellation-safe.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dream.services import cron as cron_service
from dream.tasks._cron import (
    CRON_MANIFEST_DIR,
    CronManifest,
    default_cron_manifests,
    get_cron_job,
    load_cron_jobs,
    load_cron_manifest,
    load_cron_manifests,
    save_cron_jobs,
    upsert_cron_job,
)
from dream.tasks._cron_session import CRON_RUNS_ROOT, read_cron_run_records
from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._types import TaskRecord

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _wait_until_done(
    manager: BackgroundTaskManager, task_id: str, *, timeout: float = 10.0
) -> TaskRecord:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        task = manager.get_task(task_id)
        assert task is not None
        if task.status in {"completed", "failed", "killed"}:
            return task
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"task {task_id} did not finish in {timeout}s")
        await asyncio.sleep(0.02)


def _registry_path(tmp_path: Path) -> Path:
    return tmp_path / ".dream" / "cron" / "registry.json"


def _due_manifest(name: str = "ad-hoc") -> CronManifest:
    # ``* * * * *`` fires every minute — combined with ``next_run`` in the
    # past, the tick loop treats it as immediately due.
    return CronManifest(
        name=name,
        enabled=True,
        schedule="* * * * *",
        description="ad-hoc test job",
    )


# ---------------------------------------------------------------------------
# bootstrap_default_manifests
# ---------------------------------------------------------------------------


def test_bootstrap_writes_all_four_defaults_first_time(tmp_path: Path) -> None:
    written = cron_service.bootstrap_default_manifests(tmp_path)

    expected_names = {m.name for m in default_cron_manifests()}
    assert {p.stem for p in written} == expected_names
    for name in expected_names:
        path = tmp_path / CRON_MANIFEST_DIR / f"{name}.toml"
        assert path.exists()
        # Parsing the file back yields a matching manifest.
        manifest = load_cron_manifest(path)
        assert manifest.name == name


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    cron_service.bootstrap_default_manifests(tmp_path)
    written_second = cron_service.bootstrap_default_manifests(tmp_path)
    assert written_second == []


def test_bootstrap_preserves_operator_edits(tmp_path: Path) -> None:
    cron_service.bootstrap_default_manifests(tmp_path)
    edited = tmp_path / CRON_MANIFEST_DIR / "doc-garden.toml"
    edited.write_text('name = "doc-garden"\nschedule = "0 3 * * *"\nenabled = false\n')

    cron_service.bootstrap_default_manifests(tmp_path)

    manifest = load_cron_manifest(edited)
    assert manifest.schedule == "0 3 * * *"
    assert manifest.enabled is False


# ---------------------------------------------------------------------------
# ensure_registry_seeded
# ---------------------------------------------------------------------------


def test_ensure_registry_seeded_adds_missing_manifests(tmp_path: Path) -> None:
    cron_service.bootstrap_default_manifests(tmp_path)
    registry = _registry_path(tmp_path)

    added = cron_service.ensure_registry_seeded(
        registry,
        load_cron_manifests(tmp_path / CRON_MANIFEST_DIR),
    )

    assert {j.name for j in added} == {m.name for m in default_cron_manifests()}
    assert {j.name for j in load_cron_jobs(registry)} == {
        m.name for m in default_cron_manifests()
    }


def test_ensure_registry_seeded_does_not_overwrite_existing(tmp_path: Path) -> None:
    registry = _registry_path(tmp_path)
    registry.parent.mkdir(parents=True)
    # Pre-seed: doc-garden already exists with enabled=False (operator
    # disabled it via cron_toggle).
    pre_existing = CronManifest(
        name="doc-garden",
        enabled=False,
        schedule="0 6 * * *",
    ).to_job()
    upsert_cron_job(registry, pre_existing)

    cron_service.bootstrap_default_manifests(tmp_path)
    added = cron_service.ensure_registry_seeded(
        registry,
        load_cron_manifests(tmp_path / CRON_MANIFEST_DIR),
    )

    assert "doc-garden" not in {j.name for j in added}
    persisted = get_cron_job(registry, "doc-garden")
    assert persisted is not None and persisted.enabled is False


# ---------------------------------------------------------------------------
# run_cron_kind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cron_kind_spawns_session_and_records_run(
    tmp_path: Path,
) -> None:
    # Write a tiny one-off manifest and a stub registry.
    manifest_dir = tmp_path / CRON_MANIFEST_DIR
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "ad-hoc.toml").write_text(
        'name = "ad-hoc"\nschedule = "* * * * *"\nenabled = true\n'
    )

    manager = BackgroundTaskManager(tasks_dir=tmp_path / ".dream" / "tasks")
    registry = _registry_path(tmp_path)
    cron_service.ensure_registry_seeded(
        registry, load_cron_manifests(manifest_dir)
    )

    record = await cron_service.run_cron_kind(
        kind="ad-hoc",
        working_dir=tmp_path,
        manager=manager,
        registry_path=registry,
    )

    assert record is not None
    finished = await _wait_until_done(manager, record.id)
    assert finished.status == "completed"

    runs = read_cron_run_records(tmp_path / CRON_RUNS_ROOT, "ad-hoc")
    assert len(runs) == 1
    assert runs[0].outcome == "success"

    job = get_cron_job(registry, "ad-hoc")
    assert job is not None
    assert job.last_status == "success"
    assert job.last_run is not None


@pytest.mark.asyncio
async def test_run_cron_kind_disabled_writes_skipped_record(
    tmp_path: Path,
) -> None:
    manifest_dir = tmp_path / CRON_MANIFEST_DIR
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "muted.toml").write_text(
        'name = "muted"\nschedule = "* * * * *"\nenabled = false\n'
    )

    manager = BackgroundTaskManager(tasks_dir=tmp_path / ".dream" / "tasks")
    record = await cron_service.run_cron_kind(
        kind="muted",
        working_dir=tmp_path,
        manager=manager,
    )

    assert record is None
    runs = read_cron_run_records(tmp_path / CRON_RUNS_ROOT, "muted")
    assert len(runs) == 1
    assert runs[0].outcome == "skipped"


@pytest.mark.asyncio
async def test_run_cron_kind_records_failure_from_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The registry's last_status must reflect the actual exit, not optimistic
    # post-spawn success: a session that exits non-zero is recorded failed.
    import sys

    manifest_dir = tmp_path / CRON_MANIFEST_DIR
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "ad-hoc.toml").write_text(
        'name = "ad-hoc"\nschedule = "* * * * *"\nenabled = true\n'
    )
    registry = _registry_path(tmp_path)
    cron_service.ensure_registry_seeded(registry, load_cron_manifests(manifest_dir))

    monkeypatch.setattr(
        cron_service,
        "_default_cron_argv",
        lambda manifest: [sys.executable, "-c", "import sys; sys.exit(7)"],
    )

    manager = BackgroundTaskManager(tasks_dir=tmp_path / ".dream" / "tasks")
    record = await cron_service.run_cron_kind(
        kind="ad-hoc",
        working_dir=tmp_path,
        manager=manager,
        registry_path=registry,
    )
    assert record is not None
    await _wait_until_done(manager, record.id)
    # Give the completion listener a turn to update the registry.
    for _ in range(50):
        await asyncio.sleep(0.02)
        job = get_cron_job(registry, "ad-hoc")
        if job is not None and job.last_status == "failed":
            break

    job = get_cron_job(registry, "ad-hoc")
    assert job is not None
    assert job.last_status == "failed"


@pytest.mark.asyncio
async def test_run_cron_kind_respects_registry_disable(tmp_path: Path) -> None:
    # Manifest enabled, but the registry row was toggled off (cron_toggle).
    # The run must be skipped even though the manifest itself is enabled.
    manifest_dir = tmp_path / CRON_MANIFEST_DIR
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "ad-hoc.toml").write_text(
        'name = "ad-hoc"\nschedule = "* * * * *"\nenabled = true\n'
    )
    registry = _registry_path(tmp_path)
    upsert_cron_job(
        registry,
        CronManifest(name="ad-hoc", enabled=False, schedule="* * * * *").to_job(),
    )

    manager = BackgroundTaskManager(tasks_dir=tmp_path / ".dream" / "tasks")
    record = await cron_service.run_cron_kind(
        kind="ad-hoc",
        working_dir=tmp_path,
        manager=manager,
        registry_path=registry,
    )

    assert record is None
    runs = read_cron_run_records(tmp_path / CRON_RUNS_ROOT, "ad-hoc")
    assert len(runs) == 1
    assert runs[0].outcome == "skipped"


@pytest.mark.asyncio
async def test_run_cron_kind_missing_manifest_raises(tmp_path: Path) -> None:
    manager = BackgroundTaskManager(tasks_dir=tmp_path / ".dream" / "tasks")
    with pytest.raises(FileNotFoundError):
        await cron_service.run_cron_kind(
            kind="nope",
            working_dir=tmp_path,
            manager=manager,
        )


# ---------------------------------------------------------------------------
# run_cron_cli — registry seeding on the CLI code path
# ---------------------------------------------------------------------------


def test_cron_cli_seeds_registry_from_manifests(tmp_path: Path) -> None:
    # A brand-new CLI-only setup must populate the registry (so /cron list and
    # last_run/last_status are tracked), not just bootstrap manifest files.
    from dream.repl._cron_cli import run_cron_cli

    exit_code = run_cron_cli(kind="doc-garden", working_dir=tmp_path, timeout=10)

    assert exit_code == 0
    registry = _registry_path(tmp_path)
    seeded = {j.name for j in load_cron_jobs(registry)}
    assert seeded == {m.name for m in default_cron_manifests()}
    job = get_cron_job(registry, "doc-garden")
    assert job is not None
    assert job.last_run is not None
    assert job.last_status == "success"


# ---------------------------------------------------------------------------
# cron_tick_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_loop_fires_due_job_and_advances_next_run(
    tmp_path: Path,
) -> None:
    manifest_dir = tmp_path / CRON_MANIFEST_DIR
    manifest_dir.mkdir(parents=True)
    manifest = _due_manifest("ad-hoc")
    (manifest_dir / "ad-hoc.toml").write_text(
        f'name = "{manifest.name}"\nschedule = "{manifest.schedule}"\nenabled = true\n'
    )
    registry = _registry_path(tmp_path)
    cron_service.ensure_registry_seeded(registry, [manifest])

    # Backdate ``next_run`` so the tick loop fires immediately rather than
    # waiting for the croniter-computed minute boundary.
    jobs = load_cron_jobs(registry)
    forced = [
        j.model_copy(update={"next_run": datetime.now(UTC) - timedelta(seconds=1)})
        for j in jobs
    ]
    save_cron_jobs(registry, forced)

    manager = BackgroundTaskManager(tasks_dir=tmp_path / ".dream" / "tasks")
    loop_task = asyncio.create_task(
        cron_service.cron_tick_loop(
            manager=manager,
            working_dir=tmp_path,
            registry_path=registry,
            poll_seconds=0,
        )
    )
    try:
        # Poll for the firing rather than sleeping a fixed interval — fast
        # on a healthy box, bounded on a slow one.
        for _ in range(50):
            await asyncio.sleep(0.05)
            if read_cron_run_records(tmp_path / CRON_RUNS_ROOT, "ad-hoc"):
                break
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    runs = read_cron_run_records(tmp_path / CRON_RUNS_ROOT, "ad-hoc")
    assert runs, "tick loop did not fire the due job"
    job = get_cron_job(registry, "ad-hoc")
    assert job is not None and job.next_run is not None
    assert job.next_run > datetime.now(UTC) - timedelta(seconds=1)


@pytest.mark.asyncio
async def test_tick_loop_skips_disabled_jobs(tmp_path: Path) -> None:
    manifest_dir = tmp_path / CRON_MANIFEST_DIR
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "muted.toml").write_text(
        'name = "muted"\nschedule = "* * * * *"\nenabled = false\n'
    )
    registry = _registry_path(tmp_path)
    cron_service.ensure_registry_seeded(
        registry, load_cron_manifests(manifest_dir)
    )
    jobs = load_cron_jobs(registry)
    save_cron_jobs(
        registry,
        [
            j.model_copy(
                update={"next_run": datetime.now(UTC) - timedelta(seconds=1)}
            )
            for j in jobs
        ],
    )

    manager = BackgroundTaskManager(tasks_dir=tmp_path / ".dream" / "tasks")
    loop_task = asyncio.create_task(
        cron_service.cron_tick_loop(
            manager=manager,
            working_dir=tmp_path,
            registry_path=registry,
            poll_seconds=0,
        )
    )
    try:
        await asyncio.sleep(0.3)
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    runs = read_cron_run_records(tmp_path / CRON_RUNS_ROOT, "muted")
    assert runs == []


@pytest.mark.asyncio
async def test_tick_loop_advances_next_run_when_manifest_missing(
    tmp_path: Path,
) -> None:
    # Job in registry, manifest TOML deleted — the loop must roll next_run
    # forward as a failed run instead of busy-spinning.
    manifest_dir = tmp_path / CRON_MANIFEST_DIR
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "orphan.toml").write_text(
        'name = "orphan"\nschedule = "* * * * *"\nenabled = true\n'
    )
    registry = _registry_path(tmp_path)
    cron_service.ensure_registry_seeded(
        registry, load_cron_manifests(manifest_dir)
    )
    # Backdate, then delete the manifest.
    save_cron_jobs(
        registry,
        [
            j.model_copy(
                update={"next_run": datetime.now(UTC) - timedelta(seconds=1)}
            )
            for j in load_cron_jobs(registry)
        ],
    )
    (manifest_dir / "orphan.toml").unlink()

    before = get_cron_job(registry, "orphan")
    assert before is not None and before.next_run is not None

    manager = BackgroundTaskManager(tasks_dir=tmp_path / ".dream" / "tasks")
    loop_task = asyncio.create_task(
        cron_service.cron_tick_loop(
            manager=manager,
            working_dir=tmp_path,
            registry_path=registry,
            poll_seconds=0,
        )
    )
    try:
        for _ in range(50):
            await asyncio.sleep(0.05)
            after = get_cron_job(registry, "orphan")
            if (
                after is not None
                and after.last_status == "failed"
            ):
                break
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    after = get_cron_job(registry, "orphan")
    assert after is not None
    assert after.last_status == "failed"


@pytest.mark.asyncio
async def test_tick_loop_honours_custom_argv_builder(tmp_path: Path) -> None:
    """A consumer agent supplies the real cron payload (spec 15 follow-up):
    ``argv_for`` replaces the print-stub so a scheduled job runs an actual
    one-shot command (e.g. a digest run)."""
    manifest_dir = tmp_path / CRON_MANIFEST_DIR
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "digest.toml").write_text(
        'name = "digest"\nschedule = "* * * * *"\nenabled = true\n'
    )
    registry = _registry_path(tmp_path)
    cron_service.ensure_registry_seeded(registry, load_cron_manifests(manifest_dir))
    jobs = load_cron_jobs(registry)
    save_cron_jobs(
        registry,
        [
            j.model_copy(update={"next_run": datetime.now(UTC) - timedelta(seconds=1)})
            for j in jobs
        ],
    )
    marker = tmp_path / "fired.txt"
    seen: list[str] = []

    def argv_for(manifest: CronManifest) -> list[str]:
        seen.append(manifest.name)
        return ["touch", str(marker)]

    manager = BackgroundTaskManager(tasks_dir=tmp_path / ".dream" / "tasks")
    loop_task = asyncio.create_task(
        cron_service.cron_tick_loop(
            manager=manager,
            working_dir=tmp_path,
            registry_path=registry,
            poll_seconds=0,
            argv_for=argv_for,
        )
    )
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            if marker.exists():
                break
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    assert seen == ["digest"]
    assert marker.exists(), "custom argv payload did not run"
