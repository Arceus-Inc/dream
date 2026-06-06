"""Task engine — exec-plans, runtime task records, cron (Spec 07).

Slice 1 ships the **durable exec-plan layer**: the Markdown + JSON pair
under ``docs/exec-plans/{state}/`` (:class:`ExecPlan`/:class:`Ledger`),
the plan FSM (:data:`PLAN_STATES`, :func:`move_plan`,
:func:`archive_candidates`), and the rolling append-only tech-debt
tracker (:class:`TechDebtEntry`, :func:`append_tech_debt_entry`).

Slice 2 will add the ephemeral :class:`BackgroundTaskManager` and the
runtime :class:`TaskRecord`. Slice 3 wires the cron registry +
cron-as-session.
"""

from __future__ import annotations

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
from dream.tasks._plan import (
    EXEC_PLAN_SECTIONS,
    ExecPlan,
    MissingSectionError,
    read_plan,
    write_plan,
)
from dream.tasks._tech_debt import (
    TECH_DEBT_FILENAME,
    TechDebtEntry,
    TechDebtSource,
    append_tech_debt_entry,
    tech_debt_path,
)

__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "EXEC_PLAN_SECTIONS",
    "LEDGER_SCHEMA_PATH",
    "LEDGER_SCHEMA_URI",
    "PLAN_STATES",
    "TECH_DEBT_FILENAME",
    "ExecPlan",
    "Ledger",
    "LedgerEntry",
    "LedgerEntryStatus",
    "LedgerSchemaError",
    "LedgerState",
    "LedgerStateError",
    "MissingSectionError",
    "PlanFSMError",
    "TechDebtEntry",
    "TechDebtSource",
    "advance_state",
    "append_tech_debt_entry",
    "archive_candidates",
    "move_plan",
    "plan_dir",
    "read_ledger",
    "read_plan",
    "tech_debt_path",
    "write_ledger",
    "write_plan",
]
