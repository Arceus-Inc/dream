"""Spec 07 slice 1 — rolling append-only tech-debt tracker.

A single Markdown file (``docs/exec-plans/tech-debt-tracker.md``) that the
harness *appends* findings to and *never acts on autonomously*. Each
bullet carries the five required fields:

- ``ts``  — ISO 8601 timestamp,
- ``source`` — what kind of run filed it,
- ``task_id`` — owning task id, if any,
- ``missing`` — one-line description,
- ``evidence`` — pointer (file path, ledger ref, URL).

Append-only is enforced by *reading the existing file, appending the new
bullet, and writing the whole file back through* :func:`atomic_write_text`.
That trips the Spec 01 invariant for *atomic* writes; the
*append-only* property is operator-facing — we never mutate existing
bullets, but the file as a whole is rewritten.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dream.utils.file_lock import exclusive_file_lock
from dream.utils.fs import atomic_write_text

__all__ = [
    "TECH_DEBT_FILENAME",
    "TechDebtEntry",
    "TechDebtSource",
    "append_tech_debt_entry",
    "tech_debt_path",
]


TECH_DEBT_FILENAME = "tech-debt-tracker.md"
_HEADER = "# Tech debt tracker\n\n"

TechDebtSource = Literal[
    "verification.failure",
    "refactor-deviation",
    "doc-garden",
    "manual",
]


class TechDebtEntry(BaseModel):
    """One bullet to append to the tracker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: datetime
    source: TechDebtSource
    missing: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    task_id: str | None = None

    @field_validator("missing", "evidence", "task_id")
    @classmethod
    def _no_newlines(cls, value: str | None) -> str | None:
        """Reject control characters that would break the single-line bullet.

        Each entry renders as exactly one Markdown bullet via :meth:`to_bullet`.
        A newline (or carriage return) in any free-text field would inject fake
        bullets or headings into the operator-facing tracker — a Markdown
        injection. Constrain these fields to a single line at the boundary so
        the renderer can never emit a multi-line bullet.
        """
        if value is not None and ("\n" in value or "\r" in value):
            raise ValueError("must be a single line (no newline/carriage return)")
        return value

    def to_bullet(self) -> str:
        """Render as one Markdown bullet (a single line)."""
        bits = [
            f"`{self.ts.isoformat()}`",
            f"source={self.source}",
        ]
        if self.task_id is not None:
            bits.append(f"task={self.task_id}")
        bits.append(f"missing: {self.missing}")
        bits.append(f"evidence: {self.evidence}")
        return "- " + " | ".join(bits)


def tech_debt_path(root: str | Path) -> Path:
    """Return the tracker's canonical location under ``root``."""
    return Path(root) / TECH_DEBT_FILENAME


def append_tech_debt_entry(root: str | Path, entry: TechDebtEntry) -> None:
    """Append one bullet to the tracker; create the file if needed.

    The whole file is rewritten through :func:`atomic_write_text` (Spec 01
    decision 9). Existing content is preserved verbatim — operator edits
    above the new bullet are never disturbed.

    The read→append→write runs under an exclusive file lock so concurrent
    writers serialise: without it the unlocked read-modify-write loses
    appends when two callers read the same prior content and both write back.
    """
    path = tech_debt_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with exclusive_file_lock(lock_path):
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if not existing.endswith("\n"):
                existing += "\n"
        else:
            existing = _HEADER
        atomic_write_text(path, existing + entry.to_bullet() + "\n")
