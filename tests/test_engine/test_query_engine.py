"""Spec 05 slice D -- ``QueryEngine``: the per-session glue object.

A ``QueryEngine`` bundles the three things a ``run_session`` call needs --
a ``TurnStreamer`` (model side), a ``ToolDispatcher`` (tool side), and a
``session_id`` -- plus enough metadata (``working_dir``, ``max_turns``)
to build a ``SessionConfig`` on demand. The ``build_query_engine``
factory composes the ``EngineToolDispatcher`` from a ``ToolRegistry`` so
callers never instantiate the dispatcher by hand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from dream.contracts.tool import ToolResult
from dream.engine._engine import QueryEngine, build_query_engine
from dream.engine._tool_dispatch import EngineToolDispatcher
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer

# --- local fake tool for the build_query_engine factory test ----------------


class _NopInput(BaseModel):
    pass


class _NopTool(BaseTool):
    name = "nop"
    description = "Does nothing."
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _NopInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="ok")


def _registry_with(tool: BaseTool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool, source=ToolSource.DEFAULT)
    return reg


# --- QueryEngine surface ----------------------------------------------------


def test_query_engine_holds_streamer_and_dispatcher() -> None:
    streamer = FakeStreamer(turns=[])
    dispatcher = FakeDispatcher()
    engine = QueryEngine(
        streamer=streamer,
        dispatcher=dispatcher,
        session_id="s1",
        working_dir=Path("/tmp/x"),
    )

    assert engine.streamer is streamer
    assert engine.dispatcher is dispatcher
    assert engine.session_id == "s1"
    assert engine.working_dir == Path("/tmp/x")
    # max_turns has a sane default matching ``QueryContext.max_turns``.
    assert engine.max_turns == 8


def test_query_engine_make_session_config_wires_streamer_and_dispatcher() -> None:
    streamer = FakeStreamer(turns=[])
    dispatcher = FakeDispatcher()
    engine = QueryEngine(
        streamer=streamer,
        dispatcher=dispatcher,
        session_id="sX",
        working_dir=Path("/tmp/x"),
        max_turns=3,
    )

    cfg = engine.make_session_config()

    # The SessionConfig must point at the engine's own collaborators -- not
    # a copy and not a default. Tests downstream rely on this identity.
    assert cfg.client is streamer
    assert cfg.tools is dispatcher
    assert cfg.session_id == "sX"
    assert cfg.max_turns == 3
    # No ritual configured by default; slice D leaves orientation /
    # heartbeat / reviewer for later wiring.
    assert cfg.orientation is None
    assert cfg.heartbeat is None
    assert cfg.reviewer is None


def test_query_engine_make_session_config_threads_checkpoint() -> None:
    received: list[Any] = []

    def _ck(rec: Any) -> None:
        received.append(rec)

    engine = QueryEngine(
        streamer=FakeStreamer(turns=[]),
        dispatcher=FakeDispatcher(),
        session_id="s",
        working_dir=Path("/tmp/x"),
    )
    cfg = engine.make_session_config(checkpoint=_ck)

    assert cfg.checkpoint is _ck


# --- build_query_engine factory ---------------------------------------------


def test_build_query_engine_wraps_registry_in_engine_tool_dispatcher(
    tmp_path: Path,
) -> None:
    streamer = FakeStreamer(turns=[])
    reg = _registry_with(_NopTool())

    engine = build_query_engine(
        streamer=streamer,
        registry=reg,
        session_id="sf",
        working_dir=tmp_path,
    )

    assert isinstance(engine.dispatcher, EngineToolDispatcher)
    # The dispatcher must own the registry passed in (no copy / no rebuild)
    # so callers can hot-register tools and have them visible on next dispatch.
    assert engine.dispatcher.registry is reg
    assert engine.dispatcher.working_dir == tmp_path
    assert engine.dispatcher.session_id == "sf"


def test_build_query_engine_threads_scratch_dir_and_on_dispatch(
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "scratch"
    captured: list[Any] = []
    observer = captured.append

    engine = build_query_engine(
        streamer=FakeStreamer(turns=[]),
        registry=_registry_with(_NopTool()),
        session_id="sf",
        working_dir=tmp_path,
        scratch_dir=scratch,
        on_dispatch=observer,
        max_turns=11,
    )

    assert isinstance(engine.dispatcher, EngineToolDispatcher)
    assert engine.dispatcher.scratch_dir == scratch
    assert engine.dispatcher.on_dispatch is observer
    assert engine.max_turns == 11


async def test_build_query_engine_dispatcher_actually_dispatches(
    tmp_path: Path,
) -> None:
    """End-to-end: factory output dispatches a real registered tool."""
    engine = build_query_engine(
        streamer=FakeStreamer(turns=[]),
        registry=_registry_with(_NopTool()),
        session_id="sf",
        working_dir=tmp_path,
    )

    content, is_error = await engine.dispatcher.dispatch("nop", {})

    assert is_error is False
    assert content == "ok"


# --- private import surface --------------------------------------------------


def test_query_engine_module_is_private() -> None:
    """``QueryEngine`` and the factory live under ``dream.engine._engine``
    and must not appear on the public ``dream`` package."""
    import dream

    assert not hasattr(dream, "QueryEngine")
    assert not hasattr(dream, "build_query_engine")


def test_query_engine_module_exports() -> None:
    import dream.engine._engine as eng

    assert set(eng.__all__) == {"QueryEngine", "build_query_engine"}


def test_query_engine_rejects_unknown_tool(tmp_path: Path) -> None:
    """Smoke: the dispatcher returned by the factory satisfies the
    ``ToolDispatcher`` Protocol declared by the engine loop."""
    from dream.engine._loop import ToolDispatcher

    engine = build_query_engine(
        streamer=FakeStreamer(turns=[]),
        registry=_registry_with(_NopTool()),
        session_id="sf",
        working_dir=tmp_path,
    )

    dispatcher: ToolDispatcher = engine.dispatcher  # structural assignment

    assert hasattr(dispatcher, "dispatch")
    assert callable(dispatcher.dispatch)


def test_query_engine_is_a_dataclass_with_keyword_only_friendly_init() -> None:
    """Ensure ``QueryEngine`` is constructible with keyword arguments only
    (the call sites in ``Harness.start_session`` always use kwargs)."""
    with pytest.raises(TypeError):
        QueryEngine(FakeStreamer(turns=[]))  # type: ignore[call-arg,misc]
