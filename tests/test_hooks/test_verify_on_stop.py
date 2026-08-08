"""Eval: VerifyOnStopHook (Hermes pre_verify / verify-on-stop)."""

from __future__ import annotations

import pytest

from dream.contracts.hook import HookEvent
from dream.hooks import HookExecutor
from dream.hooks._verify_on_stop import VerifyOnStopConfig, VerifyOnStopHook


@pytest.mark.asyncio
async def test_nudge_when_mutated_without_evidence() -> None:
    hook = VerifyOnStopHook()
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "write_file", "is_error": False},
    )
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert result.continue_message is not None
    assert "verif" in result.continue_message.lower() or "test" in result.continue_message.lower()


@pytest.mark.asyncio
async def test_no_nudge_when_evidence_ran_after_mutate() -> None:
    hook = VerifyOnStopHook()
    await hook(HookEvent.POST_TOOL_USE, {"tool_name": "edit_file", "is_error": False})
    await hook(HookEvent.POST_TOOL_USE, {"tool_name": "bash", "is_error": False})
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert result.continue_message is None


@pytest.mark.asyncio
async def test_no_nudge_when_nothing_mutated() -> None:
    hook = VerifyOnStopHook()
    await hook(HookEvent.POST_TOOL_USE, {"tool_name": "read_file", "is_error": False})
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert result.continue_message is None


@pytest.mark.asyncio
async def test_terminal_stop_does_not_nudge() -> None:
    hook = VerifyOnStopHook()
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "write_file", "is_error": False},
    )
    result = await hook(HookEvent.STOP, {"session_id": "s1", "phase": "terminal"})
    assert result.continue_message is None


@pytest.mark.asyncio
async def test_session_start_resets_tracking() -> None:
    hook = VerifyOnStopHook()
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "write_file", "is_error": False},
    )
    await hook(HookEvent.SESSION_START, {"session_id": "s1"})
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert result.continue_message is None


@pytest.mark.asyncio
async def test_executor_honors_allow_continue() -> None:
    hook = VerifyOnStopHook(
        config=VerifyOnStopConfig(
            nudge_template="Run pytest before finishing.",
        )
    )
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "write_file", "is_error": False},
    )
    outcome = await HookExecutor(hooks=[hook]).fire(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert outcome.continue_message == "Run pytest before finishing."


@pytest.mark.asyncio
async def test_failed_mutate_does_not_count() -> None:
    hook = VerifyOnStopHook()
    await hook(HookEvent.POST_TOOL_USE, {"tool_name": "write_file", "is_error": True})
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert result.continue_message is None


@pytest.mark.asyncio
async def test_later_mutation_requires_new_evidence() -> None:
    hook = VerifyOnStopHook()
    await hook(HookEvent.POST_TOOL_USE, {"session_id": "s1", "tool_name": "write_file"})
    await hook(HookEvent.POST_TOOL_USE, {"session_id": "s1", "tool_name": "bash"})
    await hook(HookEvent.POST_TOOL_USE, {"session_id": "s1", "tool_name": "task_create"})
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal"},
    )
    assert result.continue_message is not None


@pytest.mark.asyncio
async def test_sessions_do_not_share_verify_state() -> None:
    hook = VerifyOnStopHook()
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "write_file"},
    )
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s2", "phase": "pre_seal"},
    )
    assert result.continue_message is None


def test_spec_declares_allow_continue() -> None:
    hook = VerifyOnStopHook()
    assert hook.spec.allow_continue is True
    assert HookEvent.STOP in hook.spec.events
    assert HookEvent.POST_TOOL_USE in hook.spec.events
