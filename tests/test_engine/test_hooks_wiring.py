"""Wire spec-13 lifecycle hooks into the engine turn loop (Phase 4).

The :class:`~dream.hooks.HookExecutor` is an observer-only seam (spec 13
divergence #1: hooks never veto). This module pins where the four
lifecycle events fire during a real session:

- ``SESSION_START`` once at session start, payload ``{session_id}``;
- ``PRE_TOOL_USE`` immediately *before* each tool dispatch, payload
  ``{tool_name, tool_input}``;
- ``POST_TOOL_USE`` immediately *after* the tool result is produced,
  payload ``{tool_name, is_error, result_summary}``;
- ``STOP`` once at session end, payload ``{session_id}``.

The SACRED invariant: ``POST_TOOL_USE`` must fire *after* the
``(content, is_error)`` result exists — never between a tool_use and its
tool_result. We assert that ordering against the dispatch sequence, and
that a raising hook never breaks the turn.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from dream.contracts.hook import HookEvent, HookResult, HookSpec
from dream.contracts.tool import ToolResult
from dream.engine._engine import build_query_engine
from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)
from dream.engine._records import SessionEnd
from dream.engine._session import SessionConfig, run_session
from dream.hooks import HookExecutor
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource
from tests.test_engine._fakes import FakeStreamer, FakeTurn

# --- helpers ----------------------------------------------------------------


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _ticking_clock(step_seconds: int = 1):
    base = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)
    counter = [0]

    def now() -> datetime:
        t = base + timedelta(seconds=counter[0])
        counter[0] += step_seconds
        return t

    return now


class _RecordingHook:
    """Appends every ``(event, payload)`` it observes, in fire order."""

    def __init__(self, events: tuple[HookEvent, ...]) -> None:
        self.spec = HookSpec(events=events)
        self.seen: list[tuple[HookEvent, dict[str, Any]]] = []

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        # Copy the payload so a later in-place edit by the engine can't
        # retroactively rewrite what we recorded.
        self.seen.append((event, dict(payload)))
        return HookResult()


class _RaisingHook:
    """A faulty hook that blows up on every event it subscribes to."""

    def __init__(self, events: tuple[HookEvent, ...]) -> None:
        self.spec = HookSpec(events=events)
        self.calls = 0

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        self.calls += 1
        raise RuntimeError("boom")


_ALL_LIFECYCLE = (
    HookEvent.SESSION_START,
    HookEvent.PRE_TOOL_USE,
    HookEvent.POST_TOOL_USE,
    HookEvent.STOP,
)


class _EchoInput(BaseModel):
    pass


class _EchoTool(BaseTool):
    name = "echo"
    description = "Returns a fixed string."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _EchoInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="echo-result")


def _registry_with(tool: BaseTool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool, source=ToolSource.DEFAULT)
    return reg


def _one_tool_call_streamer() -> FakeStreamer:
    """A streamer that calls ``echo`` once, then finishes with a bare turn."""
    return FakeStreamer(
        turns=[
            FakeTurn(tool_uses=[ToolUseBlock(id="tu1", name="echo", input={})]),
            FakeTurn(text_chunks=["done"]),
        ]
    )


async def _drain(gen: Any) -> list[Any]:
    return [ev async for ev in gen]


def _config_with_executor(
    *,
    registry: ToolRegistry,
    streamer: FakeStreamer,
    executor: HookExecutor,
    working_dir: Path,
    session_id: str = "s_hooks",
) -> SessionConfig:
    engine = build_query_engine(
        streamer=streamer,
        registry=registry,
        session_id=session_id,
        working_dir=working_dir,
        max_turns=4,
        hook_executor=executor,
    )
    cfg = engine.make_session_config()
    # Deterministic clock so the records carry stable timestamps.
    return SessionConfig(
        client=cfg.client,
        tools=cfg.tools,
        max_turns=cfg.max_turns,
        session_id=cfg.session_id,
        hook_executor=cfg.hook_executor,
        now=_ticking_clock(),
    )


# --- plumbing ---------------------------------------------------------------


def test_build_query_engine_threads_hook_executor(tmp_path: Path) -> None:
    executor = HookExecutor()
    engine = build_query_engine(
        streamer=FakeStreamer(turns=[]),
        registry=_registry_with(_EchoTool()),
        session_id="s",
        working_dir=tmp_path,
        hook_executor=executor,
    )
    assert engine.hook_executor is executor
    # The dispatcher gets the same executor so PRE/POST fire around dispatch.
    assert engine.dispatcher.hook_executor is executor
    # And the SessionConfig forwards it for SESSION_START / STOP.
    assert engine.make_session_config().hook_executor is executor


def test_hook_executor_defaults_to_none(tmp_path: Path) -> None:
    """Omitting ``hook_executor`` leaves every seam unchanged (old behaviour)."""
    engine = build_query_engine(
        streamer=FakeStreamer(turns=[]),
        registry=_registry_with(_EchoTool()),
        session_id="s",
        working_dir=tmp_path,
    )
    assert engine.hook_executor is None
    assert engine.dispatcher.hook_executor is None
    assert engine.make_session_config().hook_executor is None


# --- lifecycle order --------------------------------------------------------


async def test_full_lifecycle_order(tmp_path: Path) -> None:
    """SESSION_START -> PRE_TOOL_USE -> POST_TOOL_USE -> STOP, in that order."""
    hook = _RecordingHook(_ALL_LIFECYCLE)
    executor = HookExecutor(hooks=[hook])
    cfg = _config_with_executor(
        registry=_registry_with(_EchoTool()),
        streamer=_one_tool_call_streamer(),
        executor=executor,
        working_dir=tmp_path,
        session_id="s_lifecycle",
    )

    await _drain(run_session(cfg, [_user("hi")]))

    events = [ev for ev, _ in hook.seen]
    assert events == [
        HookEvent.SESSION_START,
        HookEvent.PRE_TOOL_USE,
        HookEvent.POST_TOOL_USE,
        HookEvent.STOP,
    ]

    by_event = {ev: payload for ev, payload in hook.seen}
    assert by_event[HookEvent.SESSION_START] == {"session_id": "s_lifecycle"}
    assert by_event[HookEvent.STOP] == {"session_id": "s_lifecycle"}
    assert by_event[HookEvent.PRE_TOOL_USE]["tool_name"] == "echo"
    assert by_event[HookEvent.PRE_TOOL_USE]["tool_input"] == {}
    post = by_event[HookEvent.POST_TOOL_USE]
    assert post["tool_name"] == "echo"
    assert post["is_error"] is False
    assert "echo-result" in post["result_summary"]


async def test_session_start_and_stop_fire_even_without_tools(tmp_path: Path) -> None:
    """A toolless session still fires SESSION_START and STOP exactly once."""
    hook = _RecordingHook(_ALL_LIFECYCLE)
    executor = HookExecutor(hooks=[hook])
    cfg = _config_with_executor(
        registry=_registry_with(_EchoTool()),
        streamer=FakeStreamer(turns=[FakeTurn(text_chunks=["hello"])]),
        executor=executor,
        working_dir=tmp_path,
    )

    events = await _drain(run_session(cfg, [_user("hi")]))

    assert any(isinstance(ev, SessionEnd) for ev in events)
    seen = [ev for ev, _ in hook.seen]
    assert seen.count(HookEvent.SESSION_START) == 1
    assert seen.count(HookEvent.STOP) == 1
    assert HookEvent.PRE_TOOL_USE not in seen
    assert HookEvent.POST_TOOL_USE not in seen


# --- atom preservation ------------------------------------------------------


async def test_post_fires_after_tool_result_recorded(tmp_path: Path) -> None:
    """The SACRED atom: POST_TOOL_USE must land AFTER the dispatch result.

    The tool records its own invocation start, then returns. We assert the
    PRE hook fires before the tool body runs and the POST hook fires after
    the body produced its result — never interleaved with the result.
    """
    trace: list[str] = []

    class _TracingInput(BaseModel):
        pass

    class _TracingTool(BaseTool):
        name = "trace"
        description = "Records execution order."
        declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
        input_model = _TracingInput

        async def execute(
            self, input: dict[str, Any], ctx: ToolExecutionContext
        ) -> ToolResult:
            trace.append("tool_body")
            return ToolResult(content="traced")

    class _OrderHook:
        def __init__(self) -> None:
            self.spec = HookSpec(
                events=(HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE)
            )

        async def __call__(
            self, event: HookEvent, payload: dict[str, Any]
        ) -> HookResult:
            trace.append(str(event.value))
            return HookResult()

    executor = HookExecutor(hooks=[_OrderHook()])
    streamer = FakeStreamer(
        turns=[
            FakeTurn(tool_uses=[ToolUseBlock(id="t", name="trace", input={})]),
            FakeTurn(text_chunks=["done"]),
        ]
    )
    cfg = _config_with_executor(
        registry=_registry_with(_TracingTool()),
        streamer=streamer,
        executor=executor,
        working_dir=tmp_path,
    )

    await _drain(run_session(cfg, [_user("go")]))

    # PRE before the body, the body produces the result, POST strictly after.
    assert trace == ["pre_tool_use", "tool_body", "post_tool_use"]


# --- crash isolation --------------------------------------------------------


async def test_raising_hook_does_not_break_the_turn(tmp_path: Path) -> None:
    """A hook that raises on every event must not abort the session.

    The turn completes (the session seals ``done``), the tool still
    dispatches, and a co-registered recording hook still observes every
    lifecycle event.
    """
    raising = _RaisingHook(_ALL_LIFECYCLE)
    recording = _RecordingHook(_ALL_LIFECYCLE)
    executor = HookExecutor(hooks=[raising, recording])
    cfg = _config_with_executor(
        registry=_registry_with(_EchoTool()),
        streamer=_one_tool_call_streamer(),
        executor=executor,
        working_dir=tmp_path,
        session_id="s_crash",
    )

    events = await _drain(run_session(cfg, [_user("hi")]))

    ends = [ev for ev in events if isinstance(ev, SessionEnd)]
    assert len(ends) == 1
    assert ends[0].outcome == "done"  # the turn was NOT aborted by the crash
    # The raising hook was actually invoked for every lifecycle event ...
    assert raising.calls == 4
    # ... yet the co-registered observer still saw the full lifecycle.
    assert [ev for ev, _ in recording.seen] == [
        HookEvent.SESSION_START,
        HookEvent.PRE_TOOL_USE,
        HookEvent.POST_TOOL_USE,
        HookEvent.STOP,
    ]
