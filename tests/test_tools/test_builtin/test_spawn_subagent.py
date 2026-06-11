"""Unit tests for the spawn_subagent tool.

Tests written FIRST (RED), before implementation exists.
All harness.run_role calls are stubbed — no network hits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dream.session import SessionCost
from dream.spawn._context import (
    SPAWN_CONTEXT_KEY,
    SpawnBudget,
    SpawnContext,
)
from dream.spawn._outcome import SpawnOutcome
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.spawn_subagent import SpawnSubagentTool

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    tmp_path: Path,
    *,
    spawn_context: SpawnContext | None = None,
) -> ToolExecutionContext:
    metadata: dict[str, Any] = {}
    if spawn_context is not None:
        metadata[SPAWN_CONTEXT_KEY] = spawn_context
    return ToolExecutionContext(
        working_dir=tmp_path,
        session_id="s_parent",
        metadata=metadata,
    )


def _good_outcome(final_text: str = "child done") -> SpawnOutcome:
    return SpawnOutcome(
        final_text=final_text,
        session_id="child-001",
        cost=SessionCost(cost_usd=0.01),
        status="completed",
    )


async def _fake_spawn_success(
    task: str,
    tools: list[str] | None,
    model: str | None,
    max_turns: int | None,
) -> SpawnOutcome:
    return _good_outcome(final_text=f"result of: {task}")


async def _fake_spawn_failure(
    task: str,
    tools: list[str] | None,
    model: str | None,
    max_turns: int | None,
) -> SpawnOutcome:
    raise RuntimeError("child engine exploded")


# ---------------------------------------------------------------------------
# tool declarations
# ---------------------------------------------------------------------------


def test_tool_name_is_spawn_subagent() -> None:
    assert SpawnSubagentTool().name == "spawn_subagent"


def test_tool_is_mutating() -> None:
    assert SpawnSubagentTool().declaration.risk == "mutating"


def test_tool_tier_required_is_1() -> None:
    assert SpawnSubagentTool().declaration.tier_required == 1


def test_tool_timeout_is_600s() -> None:
    assert SpawnSubagentTool().declaration.timeout_seconds == 600.0


def test_tool_is_not_read_only() -> None:
    assert SpawnSubagentTool().is_read_only() is False


# ---------------------------------------------------------------------------
# no spawn context — graceful three-part error
# ---------------------------------------------------------------------------


async def test_no_context_returns_three_part_error(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, spawn_context=None)
    result = await SpawnSubagentTool().execute({"task": "do something"}, ctx)
    assert result.is_error is True
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_no_context_content_mentions_spawning_unavailable(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, spawn_context=None)
    result = await SpawnSubagentTool().execute({"task": "do something"}, ctx)
    assert "spawn" in result.content.lower() or "unavailable" in result.content.lower()


# ---------------------------------------------------------------------------
# budget exhausted — cap error
# ---------------------------------------------------------------------------


async def test_cap_error_on_budget_exhausted(tmp_path: Path) -> None:
    budget = SpawnBudget(cap=0)  # already exhausted
    spawn_ctx = SpawnContext(spawn=_fake_spawn_success, budget=budget)
    ctx = _make_ctx(tmp_path, spawn_context=spawn_ctx)

    result = await SpawnSubagentTool().execute({"task": "go"}, ctx)

    assert result.is_error is True
    assert "cap" in result.metadata.get("root_cause", "").lower() or "spawn cap" in result.metadata.get("root_cause", "").lower()
    assert "consolidate" in result.metadata.get("safe_retry", "").lower()
    assert "do not spawn" in result.metadata.get("stop_condition", "").lower()


async def test_cap_error_on_17th_acquire(tmp_path: Path) -> None:
    """After 16 successful spawns (budget fully consumed), the 17th returns cap error."""
    budget = SpawnBudget(cap=16)
    for _ in range(16):
        budget.acquire()

    spawn_ctx = SpawnContext(spawn=_fake_spawn_success, budget=budget)
    ctx = _make_ctx(tmp_path, spawn_context=spawn_ctx)

    result = await SpawnSubagentTool().execute({"task": "17th spawn"}, ctx)
    assert result.is_error is True
    assert result.metadata.get("root_cause") is not None


# ---------------------------------------------------------------------------
# success envelope
# ---------------------------------------------------------------------------


async def test_success_content_is_child_final_text(tmp_path: Path) -> None:
    budget = SpawnBudget(cap=1)
    spawn_ctx = SpawnContext(spawn=_fake_spawn_success, budget=budget)
    ctx = _make_ctx(tmp_path, spawn_context=spawn_ctx)

    result = await SpawnSubagentTool().execute({"task": "summarise docs"}, ctx)

    assert result.is_error is False
    assert "summarise docs" in result.content


async def test_success_structured_has_status_completed(tmp_path: Path) -> None:
    budget = SpawnBudget(cap=1)
    spawn_ctx = SpawnContext(spawn=_fake_spawn_success, budget=budget)
    ctx = _make_ctx(tmp_path, spawn_context=spawn_ctx)

    result = await SpawnSubagentTool().execute({"task": "do it"}, ctx)

    assert result.structured is not None
    assert result.structured["status"] == "completed"


async def test_success_structured_has_child_session_id(tmp_path: Path) -> None:
    budget = SpawnBudget(cap=1)
    spawn_ctx = SpawnContext(spawn=_fake_spawn_success, budget=budget)
    ctx = _make_ctx(tmp_path, spawn_context=spawn_ctx)

    result = await SpawnSubagentTool().execute({"task": "work"}, ctx)

    assert result.structured is not None
    assert result.structured.get("child_session_id") == "child-001"


async def test_success_structured_has_cost_usd(tmp_path: Path) -> None:
    budget = SpawnBudget(cap=1)
    spawn_ctx = SpawnContext(spawn=_fake_spawn_success, budget=budget)
    ctx = _make_ctx(tmp_path, spawn_context=spawn_ctx)

    result = await SpawnSubagentTool().execute({"task": "work"}, ctx)

    assert result.structured is not None
    assert "cost_usd" in result.structured


# ---------------------------------------------------------------------------
# failure envelope — child error is data, never raises
# ---------------------------------------------------------------------------


async def test_child_failure_does_not_propagate_as_exception(tmp_path: Path) -> None:
    budget = SpawnBudget(cap=1)
    spawn_ctx = SpawnContext(spawn=_fake_spawn_failure, budget=budget)
    ctx = _make_ctx(tmp_path, spawn_context=spawn_ctx)

    # Must NOT raise; failure is data
    result = await SpawnSubagentTool().execute({"task": "work"}, ctx)
    assert result is not None


async def test_child_failure_has_status_failed(tmp_path: Path) -> None:
    budget = SpawnBudget(cap=1)
    spawn_ctx = SpawnContext(spawn=_fake_spawn_failure, budget=budget)
    ctx = _make_ctx(tmp_path, spawn_context=spawn_ctx)

    result = await SpawnSubagentTool().execute({"task": "work"}, ctx)

    assert result.is_error is False  # failure-as-data: is_error stays False
    assert result.structured is not None
    assert result.structured["status"] == "failed"


async def test_child_failure_content_contains_error_info(tmp_path: Path) -> None:
    budget = SpawnBudget(cap=1)
    spawn_ctx = SpawnContext(spawn=_fake_spawn_failure, budget=budget)
    ctx = _make_ctx(tmp_path, spawn_context=spawn_ctx)

    result = await SpawnSubagentTool().execute({"task": "work"}, ctx)
    assert "child engine exploded" in result.content or "failed" in result.content.lower()


# ---------------------------------------------------------------------------
# unknown tools reported in structured output
# ---------------------------------------------------------------------------


async def test_unknown_tools_reported_in_structured(tmp_path: Path) -> None:
    """Tools not in registry are reported as unknown_tools in structured output."""
    from dream.tools.builtin import default_registry

    registry = default_registry()
    requested_tools = ["read_file", "totally_nonexistent_tool_xyz"]

    async def _spawn_with_unknown_check(
        task: str,
        tools: list[str] | None,
        model: str | None,
        max_turns: int | None,
    ) -> SpawnOutcome:
        # The tool impl checks requested names against registered names
        known = {t.name for t in registry.list_tools()}
        unknown = [t for t in (tools or []) if t not in known]
        return SpawnOutcome(
            final_text="done",
            session_id="child-002",
            cost=SessionCost(),
            status="completed",
            unknown_tools=unknown,
        )

    budget = SpawnBudget(cap=1)
    spawn_ctx = SpawnContext(spawn=_spawn_with_unknown_check, budget=budget)
    ctx = _make_ctx(tmp_path, spawn_context=spawn_ctx)

    result = await SpawnSubagentTool().execute(
        {"task": "work", "tools": requested_tools}, ctx
    )

    assert result.structured is not None
    unknown = result.structured.get("unknown_tools", [])
    assert "totally_nonexistent_tool_xyz" in unknown
    assert "read_file" not in unknown


# ---------------------------------------------------------------------------
# input schema
# ---------------------------------------------------------------------------


def test_input_schema_has_task_required() -> None:
    schema = SpawnSubagentTool().input_schema()
    assert "task" in schema.get("required", [])


def test_input_schema_tools_is_optional() -> None:
    schema = SpawnSubagentTool().input_schema()
    # tools is not required
    assert "tools" not in schema.get("required", [])


def test_input_schema_model_is_optional() -> None:
    schema = SpawnSubagentTool().input_schema()
    assert "model" not in schema.get("required", [])


def test_input_schema_max_turns_is_optional() -> None:
    schema = SpawnSubagentTool().input_schema()
    assert "max_turns" not in schema.get("required", [])
