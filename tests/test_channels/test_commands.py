"""Runtime command types (spec 15 P2 §1).

Commands are micro-tools with strict schemas — no catch-all "do(...)".
Unknown types and malformed payloads are rejected at parse time.
"""

from __future__ import annotations

import pytest

from dream.channels import (
    CancelCommand,
    StatusCommand,
    SubmitTaskCommand,
    WakeCommand,
    command_from_dict,
)


def test_submit_task_round_trip() -> None:
    cmd = SubmitTaskCommand(intent="fix the CI", max_sprints=3)
    parsed = command_from_dict(cmd.to_dict())
    assert isinstance(parsed, SubmitTaskCommand)
    assert parsed.intent == "fix the CI"
    assert parsed.max_sprints == 3
    assert parsed.id  # minted

def test_cancel_round_trip() -> None:
    cmd = CancelCommand(task_id="t-123")
    parsed = command_from_dict(cmd.to_dict())
    assert isinstance(parsed, CancelCommand)
    assert parsed.task_id == "t-123"


def test_status_and_wake_round_trip() -> None:
    assert isinstance(command_from_dict(StatusCommand().to_dict()), StatusCommand)
    assert isinstance(command_from_dict(WakeCommand().to_dict()), WakeCommand)


def test_unknown_type_rejected() -> None:
    with pytest.raises(ValueError, match="unknown command type"):
        command_from_dict({"type": "do_anything", "id": "x"})


def test_submit_requires_intent() -> None:
    with pytest.raises(ValueError, match="intent"):
        SubmitTaskCommand(intent="")
    with pytest.raises(ValueError, match="intent"):
        command_from_dict({"type": "submit_task", "id": "x", "intent": ""})


def test_cancel_requires_task_id() -> None:
    with pytest.raises(ValueError, match="task_id"):
        CancelCommand(task_id="")


def test_negative_max_sprints_rejected() -> None:
    with pytest.raises(ValueError, match="max_sprints"):
        SubmitTaskCommand(intent="x", max_sprints=0)
