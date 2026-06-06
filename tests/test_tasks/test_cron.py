"""Spec 07 slice 3 — cron registry + manifests + governance gate.

These tests pin the registry surface defined in §"Cron registry &
scheduling" and §"Cron manifest" / §"Cron registry entry". The
implementation lives at :mod:`dream.tasks._cron`.

Coverage:

- ``validate_cron_expression`` / ``validate_timezone`` at write time
  (Spec 07 acceptance MUST 17).
- ``next_run_time`` always returns UTC honouring an optional IANA tz
  (MUST 18).
- ``upsert_cron_job`` writes under an exclusive file lock (MUST 19), is
  sorted by name, defaults ``enabled=True`` and computes ``next_run``.
- ``load_cron_jobs`` is tolerant of a missing or corrupt registry.
- ``set_job_enabled`` / ``mark_job_run`` / ``delete_cron_job`` /
  ``get_cron_job`` are persistent.
- ``CronManifest`` / ``load_cron_manifest`` / ``load_cron_manifests``
  enforce the manifest schema and discover the directory (MUST 21-22).
- ``default_cron_manifests`` ships ``doc-garden``, ``quality-grade``,
  ``refactor-deviation``, ``reference-refresh`` enabled (MUST 20).
- ``is_governance_path`` recognises ``core-beliefs.md`` and
  ``product-specs/`` (MUST 26 surface).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dream.tasks._cron import (
    CRON_MANIFEST_DIR,
    DEFAULT_CRON_KINDS,
    CronJob,
    CronJobError,
    CronManifest,
    default_cron_manifests,
    delete_cron_job,
    get_cron_job,
    is_governance_path,
    load_cron_jobs,
    load_cron_manifest,
    load_cron_manifests,
    mark_job_run,
    next_run_time,
    save_cron_jobs,
    set_job_enabled,
    upsert_cron_job,
    validate_cron_expression,
    validate_timezone,
)

# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


def test_validate_cron_expression_accepts_standard() -> None:
    assert validate_cron_expression("0 6 * * *") is True
    assert validate_cron_expression("*/5 * * * *") is True


def test_validate_cron_expression_rejects_garbage() -> None:
    assert validate_cron_expression("not a cron") is False
    assert validate_cron_expression("") is False


def test_validate_timezone_accepts_known_iana() -> None:
    assert validate_timezone("UTC") is True
    assert validate_timezone("America/New_York") is True


def test_validate_timezone_accepts_none_and_empty() -> None:
    assert validate_timezone(None) is True
    assert validate_timezone("") is True


def test_validate_timezone_rejects_bogus() -> None:
    assert validate_timezone("Not/A_Zone") is False


# ---------------------------------------------------------------------------
# next_run_time
# ---------------------------------------------------------------------------


def test_next_run_time_returns_utc_aware_with_no_tz() -> None:
    base = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
    nxt = next_run_time("0 * * * *", base=base)
    assert nxt.tzinfo is not None
    assert nxt.utcoffset() == base.utcoffset()  # UTC
    assert nxt == datetime(2024, 6, 1, 13, 0, tzinfo=UTC)


def test_next_run_time_honours_timezone_returning_utc() -> None:
    # 06:00 America/New_York on 2024-06-02 == 10:00 UTC (EDT, UTC-4).
    base = datetime(2024, 6, 2, 5, 0, tzinfo=UTC)
    nxt = next_run_time("0 6 * * *", base=base, tz="America/New_York")
    assert nxt.tzinfo is not None
    assert nxt == datetime(2024, 6, 2, 10, 0, tzinfo=UTC)


def test_next_run_time_invalid_expression_raises() -> None:
    with pytest.raises(CronJobError, match=r"invalid cron"):
        next_run_time("not a cron")


# ---------------------------------------------------------------------------
# registry — load / save round-trip
# ---------------------------------------------------------------------------


def test_load_cron_jobs_returns_empty_when_missing(tmp_path: Path) -> None:
    assert load_cron_jobs(tmp_path / "missing.json") == []


def test_load_cron_jobs_returns_empty_on_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_cron_jobs(path) == []


def test_save_cron_jobs_round_trip_sorted_by_name(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    jobs = [
        CronJob(name="zeta", schedule="0 6 * * *"),
        CronJob(name="alpha", schedule="0 7 * * *"),
    ]
    save_cron_jobs(path, jobs)
    loaded = load_cron_jobs(path)
    assert [j.name for j in loaded] == ["alpha", "zeta"]


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


def test_upsert_rejects_invalid_schedule(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    with pytest.raises(CronJobError, match=r"invalid cron"):
        upsert_cron_job(path, CronJob(name="bad", schedule="not cron"))


def test_upsert_rejects_invalid_timezone(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    with pytest.raises(CronJobError, match=r"invalid timezone"):
        upsert_cron_job(
            path,
            CronJob(name="bad-tz", schedule="0 6 * * *", timezone="Not/A_Zone"),
        )


def test_upsert_defaults_enabled_true_and_sets_next_run_utc(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    upsert_cron_job(path, CronJob(name="hourly", schedule="0 * * * *"))
    job = get_cron_job(path, "hourly")
    assert job is not None
    assert job.enabled is True
    assert job.next_run is not None
    assert job.next_run.tzinfo == UTC


def test_upsert_replaces_existing_job_by_name(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    upsert_cron_job(path, CronJob(name="thing", schedule="0 6 * * *"))
    upsert_cron_job(
        path, CronJob(name="thing", schedule="0 7 * * *", description="renamed")
    )
    jobs = load_cron_jobs(path)
    assert len(jobs) == 1
    assert jobs[0].schedule == "0 7 * * *"
    assert jobs[0].description == "renamed"


def test_upsert_is_file_locked_under_concurrent_writers(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"

    def add(name: str) -> None:
        upsert_cron_job(path, CronJob(name=name, schedule="0 6 * * *"))

    threads = [threading.Thread(target=add, args=(f"job-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    jobs = load_cron_jobs(path)
    names = sorted(j.name for j in jobs)
    assert names == [f"job-{i}" for i in range(8)]
    # File is still valid JSON (no torn writes).
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, list)
    assert len(parsed) == 8


# ---------------------------------------------------------------------------
# delete / get / toggle / mark
# ---------------------------------------------------------------------------


def test_delete_cron_job_returns_true_on_hit_false_on_miss(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    upsert_cron_job(path, CronJob(name="kill-me", schedule="0 6 * * *"))
    assert delete_cron_job(path, "kill-me") is True
    assert delete_cron_job(path, "kill-me") is False
    assert load_cron_jobs(path) == []


def test_set_job_enabled_toggles_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    upsert_cron_job(path, CronJob(name="quality-grade", schedule="0 6 * * *"))
    assert set_job_enabled(path, "quality-grade", enabled=False) is True
    job = get_cron_job(path, "quality-grade")
    assert job is not None and job.enabled is False
    assert set_job_enabled(path, "missing", enabled=False) is False


def test_mark_job_run_updates_last_run_and_recomputes_next_run(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    upsert_cron_job(path, CronJob(name="hourly", schedule="0 * * * *"))
    before = get_cron_job(path, "hourly")
    assert before is not None and before.next_run is not None

    mark_job_run(path, "hourly", success=True)
    after = get_cron_job(path, "hourly")
    assert after is not None
    assert after.last_status == "success"
    assert after.last_run is not None and after.last_run.tzinfo == UTC
    assert after.next_run is not None and after.next_run >= after.last_run

    mark_job_run(path, "hourly", success=False)
    last_failed = get_cron_job(path, "hourly")
    assert last_failed is not None and last_failed.last_status == "failed"


# ---------------------------------------------------------------------------
# manifest IO
# ---------------------------------------------------------------------------


def _write_toml(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_load_cron_manifest_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "doc-garden.toml"
    _write_toml(
        p,
        """
name = "doc-garden"
enabled = true
schedule = "0 6 * * *"
timezone = "America/New_York"
tier_required = "repo-write"
description = "Find stale docs and open PRs."
entry_prompt = "docs/cron/doc-garden.prompt.md"
max_session_minutes = 30
""".lstrip(),
    )
    m = load_cron_manifest(p)
    assert m.name == "doc-garden"
    assert m.enabled is True
    assert m.schedule == "0 6 * * *"
    assert m.timezone == "America/New_York"
    assert m.tier_required == "repo-write"
    assert m.max_session_minutes == 30
    assert m.entry_prompt == "docs/cron/doc-garden.prompt.md"


def test_load_cron_manifest_rejects_invalid_schedule(tmp_path: Path) -> None:
    p = tmp_path / "bad.toml"
    _write_toml(p, 'name = "bad"\nschedule = "not cron"\n')
    with pytest.raises(CronJobError, match=r"invalid cron"):
        load_cron_manifest(p)


def test_load_cron_manifest_rejects_invalid_timezone(tmp_path: Path) -> None:
    p = tmp_path / "bad.toml"
    _write_toml(
        p, 'name = "bad-tz"\nschedule = "0 6 * * *"\ntimezone = "Not/A_Zone"\n'
    )
    with pytest.raises(CronJobError, match=r"invalid timezone"):
        load_cron_manifest(p)


def test_load_cron_manifests_discovers_toml_files(tmp_path: Path) -> None:
    d = tmp_path / ".harness" / "cron"
    d.mkdir(parents=True)
    _write_toml(d / "a.toml", 'name = "a"\nschedule = "0 6 * * *"\n')
    _write_toml(d / "b.toml", 'name = "b"\nschedule = "0 7 * * *"\n')
    (d / "notes.md").write_text("ignore me", encoding="utf-8")

    found = load_cron_manifests(d)
    assert sorted(m.name for m in found) == ["a", "b"]


def test_load_cron_manifests_returns_empty_for_missing_directory(tmp_path: Path) -> None:
    assert load_cron_manifests(tmp_path / "absent") == []


def test_cron_manifest_dir_constant() -> None:
    assert CRON_MANIFEST_DIR == ".harness/cron"


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------


def test_default_cron_kinds_includes_all_four() -> None:
    names = {kind.name for kind in DEFAULT_CRON_KINDS}
    assert names == {
        "doc-garden",
        "quality-grade",
        "refactor-deviation",
        "reference-refresh",
    }


def test_default_cron_manifests_are_enabled_and_valid() -> None:
    mans = default_cron_manifests()
    assert len(mans) == 4
    for m in mans:
        assert isinstance(m, CronManifest)
        assert m.enabled is True
        assert validate_cron_expression(m.schedule)


# ---------------------------------------------------------------------------
# governance gate (#26 surface)
# ---------------------------------------------------------------------------


def test_is_governance_path_detects_core_beliefs() -> None:
    assert is_governance_path(Path("docs/core-beliefs.md")) is True
    assert is_governance_path(Path("core-beliefs.md")) is True


def test_is_governance_path_detects_product_specs_tree() -> None:
    assert is_governance_path(Path("docs/product-specs/01-thing.md")) is True
    assert is_governance_path(Path("product-specs/anything")) is True


def test_is_governance_path_ignores_normal_paths() -> None:
    assert is_governance_path(Path("src/dream/foo.py")) is False
    assert is_governance_path(Path("docs/cron/doc-garden.prompt.md")) is False
