"""Default ``plan_show`` tool — Spec 07 wiring slice.

Read-only tier-0. Looks up an exec-plan by ``task_id`` under
``{plans_root}/{state}/`` and returns the rendered Markdown plus its state
tag. When ``state`` is not given, the four FSM states are searched in
``PLAN_STATES`` order — that's also the FSM's lifecycle order, so the
first hit is the freshest copy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dream.tasks._fsm import plan_dir
from dream.tasks._ledger import Ledger
from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._plan import ExecPlan, write_plan
from dream.tasks._session import TaskSessionContext, put_task_context
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.plan_show import PlanShowTool


def _ctx(
    working_dir: Path, task_ctx: TaskSessionContext | None
) -> ToolExecutionContext:
    metadata: dict[str, object] = {}
    if task_ctx is not None:
        put_task_context(metadata, task_ctx)
    return ToolExecutionContext(
        working_dir=working_dir, session_id="s_test", metadata=metadata
    )


def _session(tmp_path: Path, *, plans_root: Path | None) -> TaskSessionContext:
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    return TaskSessionContext(manager=manager, plans_root=plans_root)


def _write_plan(plans_root: Path, *, state: str, task_id: str) -> None:
    now = datetime.now(UTC)
    ledger = Ledger(
        task_id=task_id,
        state=state,
        entries=[],
        created_at=now,
        updated_at=now,
    )
    sections = {
        "Goal": "G",
        "Why now": "WN",
        "Scope": "S",
        "Approach": "A",
        "Risks & mitigations": "R",
        "Definition of done": "DOD",
    }
    plan = ExecPlan(task_id=task_id, sections=sections, ledger=ledger)
    write_plan(plan_dir(plans_root, state=state), plan)


# --- declaration -----------------------------------------------------------


def test_plan_show_is_read_only_tier_0() -> None:
    tool = PlanShowTool()
    assert tool.name == "plan_show"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


# --- happy path ------------------------------------------------------------


async def test_plan_show_finds_active_plan(tmp_path: Path) -> None:
    plans = tmp_path / "exec-plans"
    _write_plan(plans, state="active", task_id="2024-01-01-feat")
    sc = _session(tmp_path, plans_root=plans)

    result = await PlanShowTool().execute(
        {"task_id": "2024-01-01-feat"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is False
    assert "2024-01-01-feat" in result.content
    assert "## Goal" in result.content
    assert result.metadata.get("task_id") == "2024-01-01-feat"
    assert result.metadata.get("state") == "active"


async def test_plan_show_with_explicit_state(tmp_path: Path) -> None:
    plans = tmp_path / "exec-plans"
    _write_plan(plans, state="draft", task_id="t1")
    sc = _session(tmp_path, plans_root=plans)

    result = await PlanShowTool().execute(
        {"task_id": "t1", "state": "draft"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is False
    assert result.metadata.get("state") == "draft"


async def test_plan_show_searches_states_in_lifecycle_order(tmp_path: Path) -> None:
    plans = tmp_path / "exec-plans"
    # both draft and active copies exist — the search order returns the
    # earliest lifecycle state (draft).
    _write_plan(plans, state="draft", task_id="dup")
    _write_plan(plans, state="active", task_id="dup")
    sc = _session(tmp_path, plans_root=plans)

    result = await PlanShowTool().execute({"task_id": "dup"}, _ctx(tmp_path, sc))
    assert result.is_error is False
    assert result.metadata.get("state") == "draft"


# --- structured errors -----------------------------------------------------


async def test_plan_show_unknown_task_id_is_structured_error(tmp_path: Path) -> None:
    plans = tmp_path / "exec-plans"
    sc = _session(tmp_path, plans_root=plans)
    result = await PlanShowTool().execute(
        {"task_id": "ghost"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is True
    assert "ghost" in result.content
    assert "root_cause" in result.metadata


async def test_plan_show_explicit_state_with_missing_plan_is_error(tmp_path: Path) -> None:
    plans = tmp_path / "exec-plans"
    sc = _session(tmp_path, plans_root=plans)
    result = await PlanShowTool().execute(
        {"task_id": "x", "state": "active"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_plan_show_invalid_state_is_error(tmp_path: Path) -> None:
    plans = tmp_path / "exec-plans"
    sc = _session(tmp_path, plans_root=plans)
    result = await PlanShowTool().execute(
        {"task_id": "x", "state": "nonsense"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_plan_show_missing_plans_root_is_error(tmp_path: Path) -> None:
    sc = _session(tmp_path, plans_root=None)
    result = await PlanShowTool().execute(
        {"task_id": "x"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_plan_show_missing_task_context_is_error(tmp_path: Path) -> None:
    result = await PlanShowTool().execute(
        {"task_id": "x"}, _ctx(tmp_path, None)
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_plan_show_invalid_input_raises(tmp_path: Path) -> None:
    sc = _session(tmp_path, plans_root=tmp_path / "exec-plans")
    with pytest.raises(Exception):
        await PlanShowTool().execute({}, _ctx(tmp_path, sc))
