"""Spec 07 slice 1 — exec-plan (Markdown narrative + JSON ledger pair).

An exec-plan is **two files** committed together under
``docs/exec-plans/{state}/{task-id}.{md,json}``:

- ``{task-id}.md`` — six required sections (Goal, Why now, Scope, Approach,
  Risks & mitigations, Definition of done). Human-readable rationale.
- ``{task-id}.json`` — the :class:`~dream.tasks._ledger.Ledger`.

This module defines :class:`ExecPlan`, the in-memory pair, plus
``read_plan``/``write_plan`` for round-tripping it. Markdown parsing is a
narrow split-on-``## `` walk — fancy enough to find the six sections in
any order, simple enough to round-trip cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dream.tasks._ledger import Ledger, read_ledger, write_ledger
from dream.utils.fs import atomic_write_text
from dream.utils.identifiers import checked_task_id as _checked_task_id

__all__ = [
    "EXEC_PLAN_SECTIONS",
    "ExecPlan",
    "MissingSectionError",
    "read_plan",
    "write_plan",
]


# Order is the canonical render order (Spec 07 §Artefact shapes).
EXEC_PLAN_SECTIONS: tuple[str, ...] = (
    "Goal",
    "Why now",
    "Scope",
    "Approach",
    "Risks & mitigations",
    "Definition of done",
)


class MissingSectionError(ValueError):
    """The Markdown payload is missing one of the six required sections."""


@dataclass(frozen=True)
class ExecPlan:
    """One exec-plan: the Markdown sections + the JSON ledger.

    ``sections`` maps section title (one of :data:`EXEC_PLAN_SECTIONS`) to
    its body text. Order in :data:`EXEC_PLAN_SECTIONS` is the render order;
    the dict's own iteration order is ignored by ``to_markdown``.
    """

    task_id: str
    sections: dict[str, str]
    ledger: Ledger

    def __post_init__(self) -> None:
        if self.task_id != self.ledger.task_id:
            raise ValueError(
                f"task_id {self.task_id!r} does not match ledger.task_id "
                f"{self.ledger.task_id!r}"
            )
        missing = [s for s in EXEC_PLAN_SECTIONS if s not in self.sections]
        if missing:
            raise MissingSectionError(
                f"exec-plan missing required section(s): {missing}"
            )

    # --- render / parse ---------------------------------------------------

    def to_markdown(self) -> str:
        """Render the plan as ``# {task_id}`` + six ``## {section}`` blocks."""
        chunks = [f"# {self.task_id}", ""]
        for section in EXEC_PLAN_SECTIONS:
            chunks.append(f"## {section}")
            chunks.append("")
            chunks.append(self.sections[section].rstrip())
            chunks.append("")
        return "\n".join(chunks)

    @classmethod
    def from_markdown_and_ledger(cls, markdown: str, ledger: Ledger) -> ExecPlan:
        """Parse a rendered exec-plan back into an :class:`ExecPlan`."""
        sections = _parse_sections(markdown)
        return cls(task_id=ledger.task_id, sections=sections, ledger=ledger)


def _parse_sections(markdown: str) -> dict[str, str]:
    """Split a Markdown doc on ``## `` headers and return ``{title: body}``.

    Only ``## `` headers are recognised (the ``#`` title is the task id and
    is ignored). Missing required sections surface as ``MissingSectionError``
    in :meth:`ExecPlan.__post_init__`; this helper only extracts what's there.
    """
    out: dict[str, str] = {}
    current_title: str | None = None
    current_body: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if current_title is not None:
                out[current_title] = "\n".join(current_body).strip()
            current_title = line[3:].strip()
            current_body = []
        elif current_title is not None:
            current_body.append(line)
    if current_title is not None:
        out[current_title] = "\n".join(current_body).strip()
    return out


# --- pair IO ----------------------------------------------------------------


def write_plan(directory: str | Path, plan: ExecPlan) -> None:
    """Write both halves of the plan into ``directory`` atomically."""
    safe_id = _checked_task_id(plan.task_id)
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_text(d / f"{safe_id}.md", plan.to_markdown())
    write_ledger(d / f"{safe_id}.json", plan.ledger)


def read_plan(directory: str | Path, *, task_id: str) -> ExecPlan:
    """Load both halves of the plan from ``directory``.

    ``task_id`` is validated against the shared safe-segment guard before it
    is joined into any filesystem path, so a traversal id (``../foo``) can
    never escape ``directory``. The loaded ledger's ``task_id`` must also
    match the requested one — a mismatch means the on-disk pair was
    tampered with or mis-filed and is rejected.
    """
    safe_id = _checked_task_id(task_id)
    d = Path(directory)
    md_path = d / f"{safe_id}.md"
    json_path = d / f"{safe_id}.json"
    if not md_path.exists():
        raise FileNotFoundError(f"exec-plan markdown missing: {md_path}")
    if not json_path.exists():
        raise FileNotFoundError(f"exec-plan ledger missing: {json_path}")
    markdown = md_path.read_text(encoding="utf-8")
    ledger = read_ledger(json_path)
    if ledger.task_id != safe_id:
        raise ValueError(
            f"exec-plan ledger task_id {ledger.task_id!r} does not match "
            f"requested task_id {safe_id!r} (path: {json_path})"
        )
    return ExecPlan.from_markdown_and_ledger(markdown, ledger)
