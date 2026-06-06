"""Task engine — exec-plans, runtime task records, cron (Spec 07).

Slice 1 ships the **durable exec-plan layer**: the Markdown + JSON pair
under ``docs/exec-plans/{state}/`` (:class:`ExecPlan`/:class:`Ledger`),
the plan FSM (:data:`PLAN_STATES`, :func:`move_plan`,
:func:`archive_candidates`), and the rolling append-only tech-debt
tracker (:class:`TechDebtEntry`, :func:`append_tech_debt_entry`).

Slice 2 adds the **ephemeral runtime layer**: :class:`TaskRecord` (the
in-memory handle for one supervised subprocess) and
:class:`BackgroundTaskManager` (spawn/list/stop/restart + completion
listeners). The durable↔ephemeral seam is
:func:`make_ledger_completion_listener` — a completion listener that
updates the tagged ledger entry when its background task terminates.

Slice 3 wires the cron registry + cron-as-session: :class:`CronJob` /
:class:`CronManifest` (`:mod:`dream.tasks._cron`), the run-record
artefact (:class:`CronRunRecord`), and :func:`spawn_cron_session` —
the runner entrypoint that translates a manifest into a ``local_agent``
task wired to a completion listener that writes
``docs/cron-runs/{kind}/{date}-{run_id}.json``.
"""

from __future__ import annotations

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
from dream.tasks._cron_session import (
    CRON_RUNS_ROOT,
    MAX_SESSION_MINUTES_METADATA_KEY,
    CronRunOutcome,
    CronRunRecord,
    cron_run_record_path,
    make_cron_run_listener,
    read_cron_run_records,
    spawn_cron_session,
    write_cron_run_record,
)
from dream.tasks._fsm import (
    DEFAULT_RETENTION_DAYS,
    PLAN_STATES,
    PlanFSMError,
    advance_state,
    archive_candidates,
    move_plan,
    plan_dir,
)
from dream.tasks._ledger import (
    LEDGER_SCHEMA_PATH,
    LEDGER_SCHEMA_URI,
    Ledger,
    LedgerEntry,
    LedgerEntryStatus,
    LedgerSchemaError,
    LedgerState,
    LedgerStateError,
    read_ledger,
    write_ledger,
)
from dream.tasks._manager import (
    AGENT_TASK_TYPES,
    RESTART_NOTICE,
    BackgroundTaskManager,
    CompletionListener,
)
from dream.tasks._plan import (
    EXEC_PLAN_SECTIONS,
    ExecPlan,
    MissingSectionError,
    read_plan,
    write_plan,
)
from dream.tasks._seam import make_ledger_completion_listener
from dream.tasks._tech_debt import (
    TECH_DEBT_FILENAME,
    TechDebtEntry,
    TechDebtSource,
    append_tech_debt_entry,
    tech_debt_path,
)
from dream.tasks._types import TaskRecord, TaskStatus, TaskType

__all__ = [
    "AGENT_TASK_TYPES",
    "CRON_MANIFEST_DIR",
    "CRON_RUNS_ROOT",
    "DEFAULT_CRON_KINDS",
    "DEFAULT_RETENTION_DAYS",
    "EXEC_PLAN_SECTIONS",
    "LEDGER_SCHEMA_PATH",
    "LEDGER_SCHEMA_URI",
    "MAX_SESSION_MINUTES_METADATA_KEY",
    "PLAN_STATES",
    "RESTART_NOTICE",
    "TECH_DEBT_FILENAME",
    "BackgroundTaskManager",
    "CompletionListener",
    "CronJob",
    "CronJobError",
    "CronManifest",
    "CronRunOutcome",
    "CronRunRecord",
    "ExecPlan",
    "Ledger",
    "LedgerEntry",
    "LedgerEntryStatus",
    "LedgerSchemaError",
    "LedgerState",
    "LedgerStateError",
    "MissingSectionError",
    "PlanFSMError",
    "TaskRecord",
    "TaskStatus",
    "TaskType",
    "TechDebtEntry",
    "TechDebtSource",
    "advance_state",
    "append_tech_debt_entry",
    "archive_candidates",
    "cron_run_record_path",
    "default_cron_manifests",
    "delete_cron_job",
    "get_cron_job",
    "is_governance_path",
    "load_cron_jobs",
    "load_cron_manifest",
    "load_cron_manifests",
    "make_cron_run_listener",
    "make_ledger_completion_listener",
    "mark_job_run",
    "move_plan",
    "next_run_time",
    "plan_dir",
    "read_cron_run_records",
    "read_ledger",
    "read_plan",
    "save_cron_jobs",
    "set_job_enabled",
    "spawn_cron_session",
    "tech_debt_path",
    "upsert_cron_job",
    "validate_cron_expression",
    "validate_timezone",
    "write_cron_run_record",
    "write_ledger",
    "write_plan",
]
