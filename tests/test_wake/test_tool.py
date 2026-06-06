"""Spec 06.5 slice 1 — ``HeartbeatTool`` schema + execute contract.

The tool is a real ``BaseTool`` so the provider sees its schema in the
turn's tool list, but its ``execute`` does no I/O: it validates the model's
input and returns a ``ToolResult`` whose ``structured`` payload carries
the decision shape. The wake runner usually intercepts the tool-use block
directly and bypasses dispatch, but routing through ``execute`` MUST also
produce the same payload so the tool composes with a regular dispatcher
(slice 2 will let the REPL ``/wake`` slash command exercise this path).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dream.tools._base import BaseTool
from dream.tools._context import ToolExecutionContext
from dream.wake import HeartbeatTool
from dream.wake._tool import HeartbeatInput


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=Path.cwd(), session_id="wake_test")


# --- input schema -----------------------------------------------------------


def test_heartbeat_input_accepts_run_with_tasks() -> None:
    parsed = HeartbeatInput.model_validate(
        {"action": "run", "tasks": ["ship slice 1"], "reason": "exec plan ready"}
    )
    assert parsed.action == "run"
    assert parsed.tasks == ["ship slice 1"]
    assert parsed.reason == "exec plan ready"


def test_heartbeat_input_accepts_skip_without_tasks() -> None:
    parsed = HeartbeatInput.model_validate(
        {"action": "skip", "reason": "nothing pending"}
    )
    assert parsed.action == "skip"
    assert parsed.tasks == []  # default empty list
    assert parsed.reason == "nothing pending"


def test_heartbeat_input_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError):
        HeartbeatInput.model_validate(
            {"action": "wait", "reason": "not a real action"}
        )


def test_heartbeat_input_requires_action() -> None:
    with pytest.raises(ValidationError):
        HeartbeatInput.model_validate({"reason": "no action"})


def test_heartbeat_input_requires_reason() -> None:
    with pytest.raises(ValidationError):
        HeartbeatInput.model_validate({"action": "skip"})


def test_heartbeat_input_caps_task_count_at_five() -> None:
    with pytest.raises(ValidationError):
        HeartbeatInput.model_validate(
            {
                "action": "run",
                "tasks": [f"t{i}" for i in range(6)],
                "reason": "too many",
            }
        )


def test_heartbeat_input_caps_per_task_length_at_200() -> None:
    with pytest.raises(ValidationError):
        HeartbeatInput.model_validate(
            {
                "action": "run",
                "tasks": ["x" * 201],
                "reason": "task too long",
            }
        )


def test_heartbeat_input_caps_reason_length_at_200() -> None:
    with pytest.raises(ValidationError):
        HeartbeatInput.model_validate(
            {"action": "skip", "reason": "x" * 201}
        )


# --- BaseTool wiring --------------------------------------------------------


def test_heartbeat_tool_is_a_base_tool_with_safe_risk() -> None:
    tool = HeartbeatTool()
    assert isinstance(tool, BaseTool)
    assert tool.name == "heartbeat"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


def test_heartbeat_tool_input_schema_is_published() -> None:
    api = HeartbeatTool().to_api_schema()
    assert api["name"] == "heartbeat"
    props = api["input_schema"]["properties"]
    assert "action" in props
    assert "tasks" in props
    assert "reason" in props


# --- execute ----------------------------------------------------------------


async def test_execute_run_carries_structured_payload() -> None:
    tool = HeartbeatTool()
    result = await tool.execute(
        {"action": "run", "tasks": ["a", "b"], "reason": "go"}, _ctx()
    )
    assert result.is_error is False
    assert result.structured == {
        "action": "run",
        "tasks": ["a", "b"],
        "reason": "go",
    }


async def test_execute_skip_zeroes_tasks_in_payload() -> None:
    """Spec 06.5 decision: ``tasks`` is meaningless when ``action == "skip"``.

    The tool normalizes this at execute time so downstream consumers can trust
    the payload shape without re-checking the action enum.
    """
    tool = HeartbeatTool()
    result = await tool.execute(
        {"action": "skip", "tasks": ["should be ignored"], "reason": "nothing pending"},
        _ctx(),
    )
    assert result.is_error is False
    assert result.structured == {
        "action": "skip",
        "tasks": [],
        "reason": "nothing pending",
    }


async def test_execute_returns_error_on_invalid_input() -> None:
    tool = HeartbeatTool()
    result = await tool.execute({"action": "wait", "reason": "bad"}, _ctx())
    assert result.is_error is True
