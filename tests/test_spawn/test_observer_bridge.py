"""Observer bridge and SUBAGENT_STOP hook tests.

Tests written FIRST (RED), before implementation exists.
All harness.run_role calls are monkeypatched — no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dream.contracts.hook import HookEvent, HookResult, HookSpec
from dream.runner._observer import _CapturingObserver
from dream.runner._role_session import OBSERVER_METADATA_KEY
from dream.session import SessionCost, SessionOptions
from dream.spawn._context import SPAWN_CONTEXT_KEY, SpawnContext
from dream.tools.builtin import default_registry

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _RecordingHook:
    """Records SUBAGENT_STOP payloads."""

    def __init__(self) -> None:
        self.spec = HookSpec(events=(HookEvent.SUBAGENT_STOP,))
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        self.payloads.append(dict(payload))
        return HookResult()


def _good_role_result() -> Any:
    """Fake RunRoleResult so monkeypatching run_role works."""
    from dream.runner._role_session import RunRoleResult

    return RunRoleResult(
        role="subagent",  # type: ignore[arg-type]
        session_id="child-abc",
        final_text="child output",
        cost=SessionCost(cost_usd=0.005),
        events=(),
    )


async def _good_role_result_coro(*args: Any, **kwargs: Any) -> Any:
    return _good_role_result()


# ---------------------------------------------------------------------------
# run_role stamps observer key
# ---------------------------------------------------------------------------


def test_observer_metadata_key_is_defined() -> None:
    """OBSERVER_METADATA_KEY must exist in _role_session."""
    assert isinstance(OBSERVER_METADATA_KEY, str)
    assert len(OBSERVER_METADATA_KEY) > 0


async def test_run_role_stamps_observer_on_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """harness.run_role must stamp the observer under OBSERVER_METADATA_KEY."""
    from dream import build_harness

    (tmp_path / "wt").mkdir(parents=True)

    harness = build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=tmp_path / "wt",
        env={"DREAM_HOME": str(tmp_path / "home")},
    )

    stamped_metadata: dict[str, Any] = {}

    from unittest.mock import AsyncMock, MagicMock

    async def _fake_start_session(opts: SessionOptions) -> Any:
        stamped_metadata.update(opts.metadata)
        session = MagicMock()
        session.id = "fake-session"
        session.cost = SessionCost()

        async def _empty_gen():
            return
            yield  # make async generator

        session.send = _empty_gen
        session.close = AsyncMock()
        return session

    monkeypatch.setattr(harness, "start_session", _fake_start_session)

    observer = _CapturingObserver()

    from dream.runner._role_session import run_role

    try:
        await run_role(harness, "planner", "some task", observer=observer)
    except Exception:
        pass  # session may fail; we just care about metadata stamping

    assert OBSERVER_METADATA_KEY in stamped_metadata
    assert stamped_metadata[OBSERVER_METADATA_KEY] is observer


# ---------------------------------------------------------------------------
# spawn.started / spawn.completed events emitted via emit
# ---------------------------------------------------------------------------


async def test_spawn_closure_emits_started_and_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spawn closure must emit spawn.started then spawn.completed."""
    from dream import build_harness
    from dream._factory import _run_spawn

    (tmp_path / "wt").mkdir(parents=True)
    harness = build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=tmp_path / "wt",
        env={"DREAM_HOME": str(tmp_path / "home")},
    )

    # Patch run_role inside the _factory module (that's what _run_spawn calls)
    monkeypatch.setattr(
        "dream._factory.run_role",
        _good_role_result_coro,
    )

    emitted: list[dict[str, Any]] = []

    async def _no_op_fire(payload: dict[str, Any]) -> None:
        pass

    await _run_spawn(
        harness=harness,
        tool_registry=default_registry(),
        task="do work",
        tools=None,
        child_model="test-model",
        child_max_turns=None,
        parent_session_id="s_parent",
        emit=emitted.append,
        fire_subagent_stop=_no_op_fire,
    )

    kinds = [e.get("kind") for e in emitted]
    assert "spawn.started" in kinds
    assert "spawn.completed" in kinds


async def test_spawn_started_event_has_parent_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dream import build_harness
    from dream._factory import _run_spawn

    (tmp_path / "wt").mkdir(parents=True)
    harness = build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=tmp_path / "wt",
        env={"DREAM_HOME": str(tmp_path / "home")},
    )

    monkeypatch.setattr("dream._factory.run_role", _good_role_result_coro)

    emitted: list[dict[str, Any]] = []

    async def _no_op_fire(payload: dict[str, Any]) -> None:
        pass

    await _run_spawn(
        harness=harness,
        tool_registry=default_registry(),
        task="x",
        tools=None,
        child_model="m",
        child_max_turns=None,
        parent_session_id="my-parent-id",
        emit=emitted.append,
        fire_subagent_stop=_no_op_fire,
    )

    started = next(e for e in emitted if e.get("kind") == "spawn.started")
    assert started.get("parent_session_id") == "my-parent-id"


async def test_spawn_completed_event_has_child_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dream import build_harness
    from dream._factory import _run_spawn

    (tmp_path / "wt").mkdir(parents=True)
    harness = build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=tmp_path / "wt",
        env={"DREAM_HOME": str(tmp_path / "home")},
    )

    monkeypatch.setattr("dream._factory.run_role", _good_role_result_coro)

    emitted: list[dict[str, Any]] = []

    async def _no_op_fire(payload: dict[str, Any]) -> None:
        pass

    await _run_spawn(
        harness=harness,
        tool_registry=default_registry(),
        task="x",
        tools=None,
        child_model="m",
        child_max_turns=None,
        parent_session_id="p",
        emit=emitted.append,
        fire_subagent_stop=_no_op_fire,
    )

    completed = next(e for e in emitted if e.get("kind") == "spawn.completed")
    assert completed.get("child_session_id") == "child-abc"
    assert completed.get("status") == "completed"


# ---------------------------------------------------------------------------
# SUBAGENT_STOP hook fires with payload
# ---------------------------------------------------------------------------


async def test_subagent_stop_hook_fires_after_child_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SUBAGENT_STOP hook must fire with child_session_id and status in payload."""
    from dream import build_harness

    (tmp_path / "wt").mkdir(parents=True)
    harness = build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=tmp_path / "wt",
        env={"DREAM_HOME": str(tmp_path / "home")},
    )

    recording_hook = _RecordingHook()
    harness.register_hook(recording_hook)

    # Patch run_role so no network call happens
    monkeypatch.setattr("dream._factory.run_role", _good_role_result_coro)

    # Re-build engine AFTER registering hook (hook executor reads hooks at session-build time)
    engine = harness.config._engine_factory("s_hooks", SessionOptions())  # type: ignore[misc]
    raw_ctx = engine.dispatcher.context_metadata.get(SPAWN_CONTEXT_KEY)  # type: ignore[attr-defined]
    assert isinstance(raw_ctx, SpawnContext)

    # Call the spawn closure directly
    await raw_ctx.spawn("task", None, None, None)

    assert len(recording_hook.payloads) == 1
    payload = recording_hook.payloads[0]
    assert "child_session_id" in payload
    assert "status" in payload
    assert payload["status"] == "completed"


async def test_run_spawn_checks_unknown_tools_against_live_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown-tool detection must use the harness's LIVE registry, not a fresh
    default one — otherwise MCP/plugin tools registered at runtime are
    false-flagged as unknown."""
    from dream import build_harness
    from dream._factory import _run_spawn
    from dream.tools._registry import ToolSource
    from dream.tools.builtin import default_registry

    (tmp_path / "wt").mkdir(parents=True)
    registry = default_registry()

    from pydantic import BaseModel

    from dream.contracts.tool import ToolResult
    from dream.tools._base import BaseTool, ToolDeclaration
    from dream.tools._context import ToolExecutionContext

    class _In(BaseModel):
        pass

    class _CustomTool(BaseTool):
        name = "my_custom_tool"
        description = "A runtime-registered tool."
        declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
        input_model = _In

        async def execute(
            self, input: dict[str, Any], ctx: ToolExecutionContext
        ) -> ToolResult:
            return ToolResult(content="ok")

    registry.register(_CustomTool(), source=ToolSource.PER_REPO)

    harness = build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=tmp_path / "wt",
        registry=registry,
        env={"DREAM_HOME": str(tmp_path / "home")},
    )
    monkeypatch.setattr("dream._factory.run_role", _good_role_result_coro)

    async def _no_op_fire(payload: dict[str, Any]) -> None:
        pass

    outcome = await _run_spawn(
        harness=harness,
        tool_registry=registry,
        task="x",
        tools=["my_custom_tool", "definitely_not_a_tool"],
        child_model="m",
        child_max_turns=None,
        parent_session_id="p",
        emit=None,
        fire_subagent_stop=_no_op_fire,
    )
    assert outcome.unknown_tools == ["definitely_not_a_tool"]


async def test_run_spawn_passes_observer_to_child_run_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child session must stream to the parent's observer (spec §1)."""
    from dream import build_harness
    from dream._factory import _run_spawn
    from dream.tools.builtin import default_registry

    (tmp_path / "wt").mkdir(parents=True)
    harness = build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=tmp_path / "wt",
        env={"DREAM_HOME": str(tmp_path / "home")},
    )

    seen_kwargs: dict[str, Any] = {}

    async def _capturing_run_role(*args: Any, **kwargs: Any) -> Any:
        seen_kwargs.update(kwargs)
        return _good_role_result()

    monkeypatch.setattr("dream._factory.run_role", _capturing_run_role)
    observer = _CapturingObserver()

    async def _no_op_fire(payload: dict[str, Any]) -> None:
        pass

    await _run_spawn(
        harness=harness,
        tool_registry=default_registry(),
        task="x",
        tools=None,
        child_model="m",
        child_max_turns=None,
        parent_session_id="p",
        emit=None,
        fire_subagent_stop=_no_op_fire,
        observer=observer,
    )
    assert seen_kwargs.get("observer") is observer


async def test_run_spawn_failure_still_emits_and_fires_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child crash must still emit spawn.completed(status=failed) and fire
    SUBAGENT_STOP(status=failed) before the error propagates to the tool."""
    from dream import build_harness
    from dream._factory import _run_spawn
    from dream.tools.builtin import default_registry

    (tmp_path / "wt").mkdir(parents=True)
    harness = build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=tmp_path / "wt",
        env={"DREAM_HOME": str(tmp_path / "home")},
    )

    async def _exploding_run_role(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("child engine died")

    monkeypatch.setattr("dream._factory.run_role", _exploding_run_role)

    emitted: list[dict[str, Any]] = []
    fired: list[dict[str, Any]] = []

    async def _recording_fire(payload: dict[str, Any]) -> None:
        fired.append(dict(payload))

    with pytest.raises(RuntimeError, match="child engine died"):
        await _run_spawn(
            harness=harness,
            tool_registry=default_registry(),
            task="x",
            tools=None,
            child_model="m",
            child_max_turns=None,
            parent_session_id="p",
            emit=emitted.append,
            fire_subagent_stop=_recording_fire,
        )

    completed = next(e for e in emitted if e.get("kind") == "spawn.completed")
    assert completed.get("status") == "failed"
    assert fired and fired[0].get("status") == "failed"


async def test_no_emit_when_observer_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no observer is set, emit is None and no events are emitted."""
    from dream import build_harness

    (tmp_path / "wt").mkdir(parents=True)
    harness = build_harness(
        model="test-model",
        api_key="test-key",
        working_dir=tmp_path / "wt",
        env={"DREAM_HOME": str(tmp_path / "home")},
    )

    monkeypatch.setattr("dream._factory.run_role", _good_role_result_coro)

    engine = harness.config._engine_factory("s_no_emit", SessionOptions())  # type: ignore[misc]
    raw_ctx = engine.dispatcher.context_metadata.get(SPAWN_CONTEXT_KEY)  # type: ignore[attr-defined]
    assert isinstance(raw_ctx, SpawnContext)

    # emit should be None when no observer was wired
    assert raw_ctx.emit is None

    # Calling spawn must still work (no exception)
    outcome = await raw_ctx.spawn("task", None, None, None)
    assert outcome is not None
