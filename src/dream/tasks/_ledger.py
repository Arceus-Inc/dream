"""Spec 07 slice 1 — exec-plan ledger.

The ledger is the JSON half of an exec-plan: a pydantic-validated record
of the task's identity, lifecycle state, and an ordered list of
``LedgerEntry`` items that the generator/evaluator advance over multiple
sessions. Three invariants are load-bearing and enforced at construction
time:

- at most one entry is ``in_progress`` per task (Spec 07 decision 5),
- an entry marked ``done`` under ``evaluator_enabled`` must have
  ``passes=True`` (Spec 07 decision 5),
- ledger ``notes`` are append-only (Spec 07 decision 5).

The on-disk file carries a ``$schema`` pointer so the Spec 01 session-start
validator can block on schema failures before any generator touches it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dream.utils.fs import atomic_write_text

__all__ = [
    "LEDGER_SCHEMA_PATH",
    "LEDGER_SCHEMA_URI",
    "Ledger",
    "LedgerEntry",
    "LedgerEntryStatus",
    "LedgerSchemaError",
    "LedgerState",
    "LedgerStateError",
    "read_ledger",
    "write_ledger",
]


LedgerEntryStatus = Literal["pending", "in_progress", "done", "blocked"]
LedgerState = Literal["draft", "active", "completed", "archived"]

# Pointer the on-disk JSON carries in ``$schema`` so the Spec 01 validator
# can resolve the schema file relative to the repo root.
LEDGER_SCHEMA_PATH = "docs/_schemas/exec-plan-ledger.schema.json"
LEDGER_SCHEMA_URI = f"./{LEDGER_SCHEMA_PATH}"


class LedgerStateError(ValueError):
    """A ledger invariant (single in_progress / done-needs-passes / append-only)
    was violated."""


class LedgerSchemaError(ValueError):
    """The on-disk JSON does not match the ledger schema."""


class LedgerEntry(BaseModel):
    """One ordered sub-step of work inside a task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    description: str
    steps: tuple[str, ...] = ()
    status: LedgerEntryStatus = "pending"
    passes: bool = False
    notes: tuple[str, ...] = ()

    def __setattr__(self, name: str, value: Any) -> None:
        # Pydantic frozen raises ValidationError; tests want the more
        # idiomatic AttributeError for an immutable container.
        raise AttributeError(f"{type(self).__name__} is frozen")


def _check_invariants(ledger: Ledger) -> None:
    in_progress = [e for e in ledger.entries if e.status == "in_progress"]
    if len(in_progress) > 1:
        raise LedgerStateError(
            f"at most one entry may be in_progress; found {len(in_progress)}: "
            f"{[e.id for e in in_progress]}"
        )
    if ledger.evaluator_enabled:
        for e in ledger.entries:
            if e.status == "done" and not e.passes:
                raise LedgerStateError(
                    f"entry {e.id!r} marked done under evaluator_enabled "
                    "requires passes=True"
                )


class Ledger(BaseModel):
    """The JSON half of an exec-plan (Spec 07 §Artefact shapes)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    state: LedgerState
    created_at: datetime
    updated_at: datetime
    entries: tuple[LedgerEntry, ...]
    evaluator_enabled: bool = False
    weights: dict[str, float] = Field(default_factory=dict)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is frozen")

    # --- invariants -------------------------------------------------------

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        # Run invariants OUTSIDE the pydantic validation flow so the
        # exception type is our own LedgerStateError (Spec 01 wants one
        # classifiable type), not pydantic's ValidationError.
        _check_invariants(self)

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> Ledger:
        out = super().model_validate(obj, **kwargs)
        _check_invariants(out)
        return out

    # --- inspection -------------------------------------------------------

    def in_progress_entry(self) -> LedgerEntry | None:
        for e in self.entries:
            if e.status == "in_progress":
                return e
        return None

    def _entry_index(self, entry_id: str) -> int:
        for i, e in enumerate(self.entries):
            if e.id == entry_id:
                return i
        raise LedgerStateError(f"unknown entry id: {entry_id!r}")

    # --- mutation (returns new instance) ---------------------------------

    def append_note(self, *, entry_id: str, note: str) -> Ledger:
        """Append a note to an entry; never modifies an existing note."""
        idx = self._entry_index(entry_id)
        target = self.entries[idx]
        new_entry = target.model_copy(update={"notes": (*target.notes, note)})
        new_entries = (*self.entries[:idx], new_entry, *self.entries[idx + 1 :])
        return self.model_copy(update={"entries": new_entries})

    def mark_in_progress(self, *, entry_id: str, now: datetime) -> Ledger:
        if self.in_progress_entry() is not None:
            raise LedgerStateError(
                "another entry is already in_progress; finish or block it first"
            )
        return self._replace_entry(entry_id, status="in_progress", now=now)

    def mark_done(self, *, entry_id: str, passes: bool, now: datetime) -> Ledger:
        if self.evaluator_enabled and not passes:
            raise LedgerStateError(
                f"entry {entry_id!r} cannot be marked done under evaluator_enabled "
                "without passes=True"
            )
        return self._replace_entry(entry_id, status="done", passes=passes, now=now)

    def mark_blocked(self, *, entry_id: str, now: datetime) -> Ledger:
        return self._replace_entry(entry_id, status="blocked", now=now)

    def _replace_entry(
        self,
        entry_id: str,
        *,
        status: LedgerEntryStatus,
        now: datetime,
        passes: bool | None = None,
    ) -> Ledger:
        idx = self._entry_index(entry_id)
        target = self.entries[idx]
        update: dict[str, Any] = {"status": status}
        if passes is not None:
            update["passes"] = passes
        new_entry = target.model_copy(update=update)
        new_entries = (*self.entries[:idx], new_entry, *self.entries[idx + 1 :])
        return self.model_copy(update={"entries": new_entries, "updated_at": now})

    # --- schema -----------------------------------------------------------

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """Return the JSON Schema for the ledger (used by the Spec 01
        session-start validator)."""
        return cls.model_json_schema()


def write_ledger(path: str | Path, ledger: Ledger) -> None:
    """Serialise the ledger to ``path`` atomically, with ``$schema`` set."""
    payload: dict[str, Any] = {"$schema": LEDGER_SCHEMA_URI}
    payload.update(ledger.model_dump(mode="json"))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=False))


def read_ledger(path: str | Path) -> Ledger:
    """Load the ledger from ``path``. Raises ``LedgerSchemaError`` on any
    shape mismatch so the Spec 01 validator gets one classifiable error."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LedgerSchemaError(f"ledger {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LedgerSchemaError(
            f"ledger {path} must be a JSON object, got {type(data).__name__}"
        )
    data.pop("$schema", None)
    try:
        return Ledger.model_validate(data)
    except Exception as exc:  # pydantic ValidationError or our LedgerStateError
        raise LedgerSchemaError(f"ledger {path} failed schema: {exc}") from exc
