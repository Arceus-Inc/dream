"""Spec 07 slice 1 — durable exec-plan (Markdown narrative + JSON ledger pair).

An exec-plan is **two files** committed together under
``docs/exec-plans/{state}/{task-id}.{md,json}``:

- ``{task-id}.md`` — required sections (Goal, Why now, Scope, Approach,
  Risks & mitigations, Definition of done). Human-readable.
- ``{task-id}.json`` — the ``Ledger`` (Spec 07 decisions 2-7).

This module wraps the pair: parsing required Markdown sections out of the
narrative, round-tripping them, and writing both halves atomically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dream.tasks._ledger import Ledger, LedgerEntry
from dream.tasks._plan import (
    EXEC_PLAN_SECTIONS,
    ExecPlan,
    MissingSectionError,
    read_plan,
    write_plan,
)


def _t() -> datetime:
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


def _ledger() -> Ledger:
    return Ledger(
        task_id="T1",
        state="active",
        created_at=_t(),
        updated_at=_t(),
        evaluator_enabled=False,
        weights={},
        entries=(LedgerEntry(id="a", description="d", steps=("s1",)),),
    )


def _full_markdown() -> str:
    body = ["# T1\n"]
    for section in EXEC_PLAN_SECTIONS:
        body.append(f"## {section}\n\n…content for {section}…\n")
    return "\n".join(body)


def _plan() -> ExecPlan:
    return ExecPlan(
        task_id="T1",
        sections={s: f"…content for {s}…" for s in EXEC_PLAN_SECTIONS},
        ledger=_ledger(),
    )


# --- shape ------------------------------------------------------------------


def test_required_sections_are_the_six_from_spec() -> None:
    """Spec 07 §Artefact shapes: exactly six sections, in this order."""
    assert EXEC_PLAN_SECTIONS == (
        "Goal",
        "Why now",
        "Scope",
        "Approach",
        "Risks & mitigations",
        "Definition of done",
    )


def test_plan_requires_all_sections() -> None:
    with pytest.raises(MissingSectionError, match="Approach"):
        ExecPlan(
            task_id="T1",
            sections={
                s: "x" for s in EXEC_PLAN_SECTIONS if s != "Approach"
            },
            ledger=_ledger(),
        )


def test_plan_task_id_must_match_ledger_task_id() -> None:
    led = _ledger()
    with pytest.raises(ValueError, match="task_id"):
        ExecPlan(
            task_id="OTHER",
            sections={s: "x" for s in EXEC_PLAN_SECTIONS},
            ledger=led,
        )


# --- markdown render + parse roundtrip --------------------------------------


def test_render_markdown_contains_each_section_as_h2() -> None:
    md = _plan().to_markdown()
    for section in EXEC_PLAN_SECTIONS:
        assert f"## {section}" in md


def test_parse_markdown_extracts_sections() -> None:
    plan = ExecPlan.from_markdown_and_ledger(_full_markdown(), _ledger())
    for section in EXEC_PLAN_SECTIONS:
        assert section in plan.sections


def test_parse_markdown_rejects_missing_section() -> None:
    bad = _full_markdown().replace("## Approach\n\n…content for Approach…\n", "")
    with pytest.raises(MissingSectionError, match="Approach"):
        ExecPlan.from_markdown_and_ledger(bad, _ledger())


def test_render_then_parse_roundtrip_preserves_section_bodies() -> None:
    plan = _plan()
    md = plan.to_markdown()
    rebuilt = ExecPlan.from_markdown_and_ledger(md, plan.ledger)
    assert rebuilt.sections == plan.sections


# --- pair IO ----------------------------------------------------------------


def test_write_plan_creates_both_files(tmp_path: Path) -> None:
    write_plan(tmp_path, _plan())
    assert (tmp_path / "T1.md").exists()
    assert (tmp_path / "T1.json").exists()


def test_read_plan_loads_the_pair(tmp_path: Path) -> None:
    plan = _plan()
    write_plan(tmp_path, plan)
    out = read_plan(tmp_path, task_id="T1")
    assert out == plan


def test_read_plan_missing_markdown_raises(tmp_path: Path) -> None:
    plan = _plan()
    write_plan(tmp_path, plan)
    (tmp_path / "T1.md").unlink()
    with pytest.raises(FileNotFoundError, match=r"T1\.md"):
        read_plan(tmp_path, task_id="T1")


def test_read_plan_missing_ledger_raises(tmp_path: Path) -> None:
    plan = _plan()
    write_plan(tmp_path, plan)
    (tmp_path / "T1.json").unlink()
    with pytest.raises(FileNotFoundError, match=r"T1\.json"):
        read_plan(tmp_path, task_id="T1")
