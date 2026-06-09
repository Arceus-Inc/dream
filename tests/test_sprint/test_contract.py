"""Tests for the sprint contract artefact + path helpers.

Spec 10 §"Sprint contract" + acceptance criterion #7 (contract written
before any source-file commit). This module covers shape + path math
only; the negotiation that produces the criteria lives in test_negotiation,
and the orchestration ordering is exercised in test_lifecycle.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_sprint_contract_round_trips_via_to_dict_from_dict() -> None:
    from dream.sprint import NegotiationEntry, SprintContract

    c = SprintContract(
        task_id="t1",
        sprint_number=2,
        goal="Add foo to bar",
        scope_includes=("change A", "change B"),
        scope_excludes=("don't touch C",),
        acceptance_criteria=("MUST x", "SHOULD y"),
        verification_steps=(
            {"kind": "test", "ref": "tests/test_foo.py::test_bar"},
            {"kind": "lint", "ref": "ruff check src/foo.py"},
        ),
        evaluator_enabled=True,
        imposed=False,
        negotiation_log=(
            NegotiationEntry(ts="2026-01-01T00:00:00", from_role="evaluator", to_role="generator", message="propose: MUST x"),
            NegotiationEntry(ts="2026-01-01T00:00:01", from_role="generator", to_role="evaluator", message="accept"),
        ),
    )
    rt = SprintContract.from_dict(c.to_dict())
    assert rt == c


def test_sprint_contract_rejects_unknown_verification_kind() -> None:
    from dream.sprint import SprintContract

    with pytest.raises(ValueError, match=r"verification|kind"):
        SprintContract(
            task_id="t1",
            sprint_number=1,
            goal="g",
            acceptance_criteria=("MUST x",),
            verification_steps=({"kind": "bogus", "ref": "x"},),
        )


def test_sprint_contract_requires_at_least_one_acceptance_criterion() -> None:
    from dream.sprint import SprintContract

    with pytest.raises(ValueError, match="acceptance"):
        SprintContract(
            task_id="t1",
            sprint_number=1,
            goal="g",
            acceptance_criteria=(),
            verification_steps=({"kind": "test", "ref": "x"},),
        )


def test_sprint_contract_save_writes_json_atomically(tmp_path: Path) -> None:
    from dream.sprint import SprintContract

    c = SprintContract(
        task_id="t1",
        sprint_number=1,
        goal="g",
        acceptance_criteria=("MUST x",),
        verification_steps=({"kind": "test", "ref": "x"},),
    )
    p = tmp_path / "t1-sprint-1.json"
    c.save(p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["task_id"] == "t1"
    assert data["sprint_number"] == 1
    assert data["imposed"] is False
    assert data["evaluator_enabled"] is True


def test_sprint_contract_load_round_trips_from_disk(tmp_path: Path) -> None:
    from dream.sprint import SprintContract

    c = SprintContract(
        task_id="t1",
        sprint_number=1,
        goal="g",
        acceptance_criteria=("MUST x",),
        verification_steps=({"kind": "test", "ref": "x"},),
    )
    p = tmp_path / "t1-sprint-1.json"
    c.save(p)
    assert SprintContract.load(p) == c


def test_sprint_contract_path_under_exec_plans_active(tmp_path: Path) -> None:
    from dream.sprint import sprint_contract_path

    p = sprint_contract_path(tmp_path, task_id="t1", sprint_number=3)
    assert p == tmp_path / "docs" / "exec-plans" / "active" / "t1-sprint-3.json"


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "/abs", "a\x00b"])
def test_sprint_contract_path_rejects_unsafe_task_id(tmp_path: Path, bad: str) -> None:
    from dream.sprint import sprint_contract_path

    with pytest.raises(ValueError, match=r"task_id|unsafe"):
        sprint_contract_path(tmp_path, task_id=bad, sprint_number=1)


@pytest.mark.parametrize("bad", [0, -1, -42])
def test_sprint_contract_path_rejects_non_positive_sprint(tmp_path: Path, bad: int) -> None:
    from dream.sprint import sprint_contract_path

    with pytest.raises(ValueError, match="sprint"):
        sprint_contract_path(tmp_path, task_id="t1", sprint_number=bad)


def test_tech_debt_path_under_exec_plans(tmp_path: Path) -> None:
    from dream.sprint import tech_debt_path

    assert tech_debt_path(tmp_path) == tmp_path / "docs" / "exec-plans" / "tech-debt-tracker.md"


def _minimal_contract_dict() -> dict:
    return {
        "task_id": "t1",
        "sprint_number": 1,
        "goal": "g",
        "acceptance_criteria": ["MUST x"],
        "verification_steps": [{"kind": "test", "ref": "x"}],
    }


@pytest.mark.parametrize("field", ["evaluator_enabled", "imposed"])
def test_from_dict_parses_real_booleans(field: str) -> None:
    from dream.sprint import SprintContract

    data = _minimal_contract_dict()
    data[field] = False
    contract = SprintContract.from_dict(data)
    assert getattr(contract, field) is False


@pytest.mark.parametrize("field", ["evaluator_enabled", "imposed"])
def test_from_dict_does_not_coerce_string_false_to_true(field: str) -> None:
    """``bool("false")`` is ``True`` — a malformed contract must not silently
    flip the flag. Strict parsing rejects non-bool values instead."""
    from dream.sprint import SprintContract

    data = _minimal_contract_dict()
    data[field] = "false"
    with pytest.raises((TypeError, ValueError)):
        SprintContract.from_dict(data)
