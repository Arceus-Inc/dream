"""Spec 07 slice 3 — cron registry, manifests, and governance gate.

Two layers:

- **Registry layer** (durable JSON registry under
  ``.dream/cron/registry.json`` or whatever the caller chooses). Jobs are
  persisted as a sorted JSON list under an exclusive file lock so two
  ``cron_create`` calls cannot tear the file. ``next_run`` is always
  stored as a UTC ISO 8601 timestamp.

- **Manifest layer** (operator-extensible TOML under
  ``.harness/cron/{kind}.toml``). The four default kinds — ``doc-garden``,
  ``quality-grade``, ``refactor-deviation``, ``reference-refresh`` —
  are shipped via :data:`DEFAULT_CRON_KINDS`. Operators add per-repo
  kinds by dropping a TOML file in the same directory.

Borrowed from OpenHarness ``src/openharness/services/cron.py``
(``validate_cron_expression``, ``validate_timezone``, ``next_run_time``,
``upsert_cron_job``, ``mark_job_run``); rewritten to use frozen pydantic
``CronJob``/``CronManifest`` instead of raw dicts, to take an explicit
``registry_path`` instead of a global, and to route writes through
:func:`dream.utils.fs.atomic_write_text` (Spec 01 invariant 9).
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from croniter import croniter  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, field_validator

from dream.utils.file_lock import exclusive_file_lock
from dream.utils.fs import atomic_write_text

__all__ = [
    "CRON_MANIFEST_DIR",
    "DEFAULT_CRON_KINDS",
    "CronJob",
    "CronJobError",
    "CronManifest",
    "default_cron_manifests",
    "delete_cron_job",
    "get_cron_job",
    "is_governance_path",
    "load_cron_jobs",
    "load_cron_manifest",
    "load_cron_manifests",
    "mark_job_run",
    "next_run_time",
    "save_cron_jobs",
    "set_job_enabled",
    "upsert_cron_job",
    "validate_cron_expression",
    "validate_timezone",
]


CRON_MANIFEST_DIR = ".harness/cron"
"""Repo-relative directory the runner scans for cron manifests."""

GOVERNANCE_PATH_MARKERS: tuple[str, ...] = ("core-beliefs.md", "product-specs")
"""Path fragments that mark a file as governance-tier (Spec 07 MUST 26).

Cron jobs that touch these paths must open a ``[governance]``-tagged PR
instead of committing to base."""


class CronJobError(ValueError):
    """A cron schedule, timezone, or manifest failed validation."""


# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


def validate_cron_expression(expression: str) -> bool:
    """Return True if ``expression`` is a valid croniter schedule."""
    if not expression:
        return False
    return bool(croniter.is_valid(expression))


def validate_timezone(tz: str | None) -> bool:
    """Return True if ``tz`` is empty/None or a valid IANA timezone."""
    if not tz:
        return True
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
    except Exception:
        return False
    return True


def next_run_time(
    expression: str,
    *,
    base: datetime | None = None,
    tz: str | None = None,
) -> datetime:
    """Return the next fire time for ``expression`` as a UTC-aware datetime.

    If ``tz`` is provided, the expression is interpreted in that IANA
    timezone (so ``0 6 * * *`` means 06:00 local). The result is always
    converted back to UTC before being returned so it can be sorted and
    compared without ambiguity.
    """
    if not validate_cron_expression(expression):
        raise CronJobError(f"invalid cron expression: {expression!r}")
    if tz and not validate_timezone(tz):
        raise CronJobError(f"invalid timezone: {tz!r}")

    base = base or datetime.now(UTC)
    if tz:
        from zoneinfo import ZoneInfo

        local_base = base.astimezone(ZoneInfo(tz))
        local_next: datetime = croniter(expression, local_base).get_next(datetime)
        return local_next.astimezone(UTC)
    nxt: datetime = croniter(expression, base).get_next(datetime)
    return nxt


# ---------------------------------------------------------------------------
# CronJob — the persisted registry entry
# ---------------------------------------------------------------------------


class CronJob(BaseModel):
    """One cron-registry entry. Frozen — toggling/scheduling go through
    helpers below that return a new instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    schedule: str
    timezone: str | None = None
    enabled: bool = True
    created_at: datetime | None = None
    next_run: datetime | None = None
    last_run: datetime | None = None
    last_status: str | None = None
    tier_required: str | None = None
    description: str | None = None
    max_session_minutes: int | None = None
    entry_prompt: str | None = None

    @field_validator("name")
    @classmethod
    def _non_empty_name(cls, v: str) -> str:
        if not v:
            raise CronJobError("cron job name must be non-empty")
        return v


def _parse_job(payload: dict[str, Any]) -> CronJob | None:
    try:
        return CronJob.model_validate(payload)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# registry IO
# ---------------------------------------------------------------------------


def _registry_lock_path(registry_path: Path) -> Path:
    return registry_path.with_suffix(registry_path.suffix + ".lock")


def load_cron_jobs(registry_path: str | Path) -> list[CronJob]:
    """Load and parse the registry. Tolerant of missing or corrupt files."""
    path = Path(registry_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    jobs: list[CronJob] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        job = _parse_job(raw)
        if job is not None:
            jobs.append(job)
    return jobs


def save_cron_jobs(registry_path: str | Path, jobs: Iterable[CronJob]) -> None:
    """Persist ``jobs`` to ``registry_path`` (sorted by name, atomic)."""
    ordered = sorted(jobs, key=lambda j: j.name)
    payload = [json.loads(j.model_dump_json(exclude_none=True)) for j in ordered]
    atomic_write_text(
        Path(registry_path),
        json.dumps(payload, indent=2, default=str) + "\n",
    )


def upsert_cron_job(registry_path: str | Path, job: CronJob) -> CronJob:
    """Insert or replace ``job`` in the registry.

    Validates ``schedule`` and ``timezone`` before any I/O; computes
    ``next_run`` (UTC) and stamps ``created_at`` if absent. The
    registry write is serialised by an OS-level exclusive file lock.
    """
    if not validate_cron_expression(job.schedule):
        raise CronJobError(f"invalid cron expression: {job.schedule!r}")
    if not validate_timezone(job.timezone):
        raise CronJobError(f"invalid timezone: {job.timezone!r}")

    now = datetime.now(UTC)
    nxt = next_run_time(job.schedule, base=now, tz=job.timezone)
    materialised = job.model_copy(
        update={
            "created_at": job.created_at or now,
            "next_run": nxt,
        }
    )

    registry = Path(registry_path)
    registry.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(_registry_lock_path(registry)):
        existing = [j for j in load_cron_jobs(registry) if j.name != materialised.name]
        existing.append(materialised)
        save_cron_jobs(registry, existing)
    return materialised


def delete_cron_job(registry_path: str | Path, name: str) -> bool:
    """Remove the job with the given ``name``; return whether it existed."""
    registry = Path(registry_path)
    if not registry.exists():
        return False
    with exclusive_file_lock(_registry_lock_path(registry)):
        before = load_cron_jobs(registry)
        after = [j for j in before if j.name != name]
        if len(after) == len(before):
            return False
        save_cron_jobs(registry, after)
    return True


def get_cron_job(registry_path: str | Path, name: str) -> CronJob | None:
    for job in load_cron_jobs(registry_path):
        if job.name == name:
            return job
    return None


def _mutate_job(
    registry_path: str | Path,
    name: str,
    mutate: Callable[[CronJob], CronJob],
) -> bool:
    """Replace the job named ``name`` with ``mutate(job)`` under the registry lock.

    Returns False (no write) if the registry is missing or ``name`` is unknown.
    Centralises the exists-check → lock → load → replace-by-name → save shape
    shared by :func:`set_job_enabled` and :func:`mark_job_run`.
    """
    registry = Path(registry_path)
    if not registry.exists():
        return False
    with exclusive_file_lock(_registry_lock_path(registry)):
        jobs = load_cron_jobs(registry)
        hit = False
        new_jobs: list[CronJob] = []
        for j in jobs:
            if j.name == name:
                hit = True
                new_jobs.append(mutate(j))
            else:
                new_jobs.append(j)
        if not hit:
            return False
        save_cron_jobs(registry, new_jobs)
    return True


def set_job_enabled(registry_path: str | Path, name: str, *, enabled: bool) -> bool:
    """Toggle a job's ``enabled`` flag. Return False if the job is unknown."""
    return _mutate_job(
        registry_path, name, lambda j: j.model_copy(update={"enabled": enabled})
    )


def mark_job_run(registry_path: str | Path, name: str, *, success: bool) -> bool:
    """Record a completed run: stamp ``last_run`` / ``last_status`` and
    recompute ``next_run`` from now. Return whether the job was found."""
    now = datetime.now(UTC)

    def _stamp(j: CronJob) -> CronJob:
        return j.model_copy(
            update={
                "last_run": now,
                "last_status": "success" if success else "failed",
                "next_run": next_run_time(j.schedule, base=now, tz=j.timezone),
            }
        )

    return _mutate_job(registry_path, name, _stamp)


# ---------------------------------------------------------------------------
# manifest layer
# ---------------------------------------------------------------------------


class CronManifest(BaseModel):
    """Operator-authored cron manifest (``.harness/cron/{kind}.toml``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    enabled: bool = True
    schedule: str
    timezone: str | None = None
    tier_required: str | None = None
    description: str | None = None
    entry_prompt: str | None = None
    max_session_minutes: int | None = None
    # Where a firing goes (spec 15 hardening 2): "spawn" runs a detached
    # task; "next-wake" queues a note the wake scheduler delivers on the
    # next heartbeat (the timed-note pattern).
    target: Literal["spawn", "next-wake"] = "spawn"

    @field_validator("schedule")
    @classmethod
    def _check_schedule(cls, v: str) -> str:
        if not validate_cron_expression(v):
            raise CronJobError(f"invalid cron expression: {v!r}")
        return v

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v: str | None) -> str | None:
        if not validate_timezone(v):
            raise CronJobError(f"invalid timezone: {v!r}")
        return v

    def to_job(self) -> CronJob:
        """Project this manifest into a registry :class:`CronJob` shell.

        ``upsert_cron_job`` fills in ``created_at`` and ``next_run``.
        """
        return CronJob(
            name=self.name,
            schedule=self.schedule,
            timezone=self.timezone,
            enabled=self.enabled,
            tier_required=self.tier_required,
            description=self.description,
            entry_prompt=self.entry_prompt,
            max_session_minutes=self.max_session_minutes,
        )


def load_cron_manifest(path: str | Path) -> CronManifest:
    """Parse a single ``.toml`` manifest into a :class:`CronManifest`."""
    p = Path(path)
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    try:
        return CronManifest.model_validate(raw)
    except CronJobError:
        raise
    except Exception as exc:  # pragma: no cover - pydantic detail
        raise CronJobError(f"invalid cron manifest at {p}: {exc}") from exc


def load_cron_manifests(directory: str | Path) -> list[CronManifest]:
    """Discover and parse every ``*.toml`` manifest in ``directory``."""
    d = Path(directory)
    if not d.is_dir():
        return []
    return sorted(
        (load_cron_manifest(p) for p in d.glob("*.toml")),
        key=lambda m: m.name,
    )


# ---------------------------------------------------------------------------
# defaults (Spec 07 MUST 20)
# ---------------------------------------------------------------------------


DEFAULT_CRON_KINDS: tuple[CronManifest, ...] = (
    CronManifest(
        name="doc-garden",
        enabled=True,
        schedule="0 6 * * *",
        tier_required="repo-write",
        description="Find stale docs and open PRs; archive completed plans past retention.",
        entry_prompt="docs/cron/doc-garden.prompt.md",
        max_session_minutes=30,
    ),
    CronManifest(
        name="quality-grade",
        enabled=True,
        schedule="0 7 * * 1",  # weekly Mondays 07:00
        tier_required="repo-read",
        description="Grade recent commits against the quality rubric.",
        entry_prompt="docs/cron/quality-grade.prompt.md",
        max_session_minutes=45,
    ),
    CronManifest(
        name="refactor-deviation",
        enabled=True,
        schedule="0 8 * * *",
        tier_required="repo-write",
        description="Scan for core-belief drift; small PRs or tech-debt entries.",
        entry_prompt="docs/cron/refactor-deviation.prompt.md",
        max_session_minutes=30,
    ),
    CronManifest(
        name="reference-refresh",
        enabled=True,
        schedule="0 9 * * 2",  # weekly Tuesdays 09:00
        tier_required="repo-write",
        description="Re-fetch vendored references under docs/references/.",
        entry_prompt="docs/cron/reference-refresh.prompt.md",
        max_session_minutes=20,
    ),
)


def default_cron_manifests() -> list[CronManifest]:
    """Return a fresh list copy of :data:`DEFAULT_CRON_KINDS`."""
    return list(DEFAULT_CRON_KINDS)


# ---------------------------------------------------------------------------
# governance gate (#26 surface)
# ---------------------------------------------------------------------------


def is_governance_path(path: str | Path) -> bool:
    """Return True if ``path`` lives under a governance-tier marker.

    Cron jobs that would modify such a path must open a
    ``[governance]``-tagged PR instead of committing to base
    (Spec 07 MUST 26 / Spec 10 / Spec 13).
    """
    parts = Path(path).as_posix().split("/")
    if not parts:
        return False
    if parts[-1] == "core-beliefs.md":
        return True
    return "product-specs" in parts
