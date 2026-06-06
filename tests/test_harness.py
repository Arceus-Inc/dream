"""Spec 05 slice D -- ``Harness.start_session`` binds an engine to the Session.

For slice D the ``Harness`` exposes a private factory hook on
``HarnessConfig`` (``_engine_factory``) that builds a ``QueryEngine`` per
session. When the hook is set the returned ``Session`` is bound to the
engine and ``send`` is real. When the hook is unset the existing
placeholder behaviour is preserved -- ``start_session`` still returns a
``Session`` whose ``send`` raises ``NotImplementedError``. The hook is
underscore-prefixed because the production wiring (Provider ->
TurnStreamer adapter from Spec 02) lands in REPL upgrade #2.

These tests also pin the public surface: ``Harness`` and ``HarnessConfig``
stay in ``dream`` exactly as before; no new public symbols are added.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.engine._cost import UsageSnapshot
from dream.engine._engine import QueryEngine
from dream.events import TextDelta, TurnComplete
from dream.harness import Harness, HarnessConfig
from dream.session import Session, SessionOptions
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn


def _engine_factory(streamer: FakeStreamer, dispatcher: FakeDispatcher | None = None):
    dispatcher = dispatcher or FakeDispatcher()

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=dispatcher,
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    return _factory


# --- factory hook plumbing ---------------------------------------------------


async def test_start_session_uses_engine_factory_when_set() -> None:
    streamer = FakeStreamer(
        turns=[FakeTurn(text_chunks=["hi"], usage=UsageSnapshot(input_tokens=1))]
    )
    config = HarnessConfig(_engine_factory=_engine_factory(streamer))  # type: ignore[call-arg]
    harness = Harness(config)

    session = await harness.start_session()

    events = []
    async for ev in session.send("p"):
        events.append(ev)

    assert any(isinstance(e, TextDelta) for e in events)
    assert any(isinstance(e, TurnComplete) for e in events)


async def test_start_session_passes_session_id_and_options_to_factory() -> None:
    captured: dict[str, object] = {}

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        captured["session_id"] = session_id
        captured["options"] = options
        return QueryEngine(
            streamer=FakeStreamer(turns=[]),
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
        )

    config = HarnessConfig(_engine_factory=_factory)  # type: ignore[call-arg]
    harness = Harness(config)

    opts = SessionOptions(model="m", max_turns=7)
    session = await harness.start_session(opts)

    assert captured["options"] is opts
    assert isinstance(captured["session_id"], str)
    assert captured["session_id"] == session.id


async def test_start_session_returns_session_bound_to_engine() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["x"])])
    config = HarnessConfig(_engine_factory=_engine_factory(streamer))  # type: ignore[call-arg]
    harness = Harness(config)

    session = await harness.start_session()

    # Confirm the binding happened: the underscore-prefixed attribute is
    # set to the factory-produced QueryEngine, not ``None``.
    assert isinstance(session, Session)
    assert getattr(session, "_engine", None) is not None
    assert isinstance(session._engine, QueryEngine)


async def test_start_session_without_factory_preserves_placeholder() -> None:
    harness = Harness(HarnessConfig())  # no factory hook

    session = await harness.start_session()

    # Same as today: a Session with no engine, ``send`` raises.
    assert getattr(session, "_engine", None) is None
    with pytest.raises(NotImplementedError):
        async for _ in session.send("p"):
            break


async def test_each_start_session_gets_a_fresh_engine() -> None:
    """The factory is called once per session, so two sessions hold
    independent engine state. This matters for transcript isolation."""
    calls: list[str] = []

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        calls.append(session_id)
        return QueryEngine(
            streamer=FakeStreamer(turns=[]),
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
        )

    config = HarnessConfig(_engine_factory=_factory)  # type: ignore[call-arg]
    harness = Harness(config)

    s1 = await harness.start_session()
    s2 = await harness.start_session()

    assert s1.id != s2.id
    assert calls == [s1.id, s2.id]


# --- HarnessConfig surface ---------------------------------------------------


def test_harness_config_engine_factory_defaults_to_none() -> None:
    config = HarnessConfig()
    # The hook is private. We only assert defaulted-None presence so we
    # don't pin its exact attribute path; behaviour is verified above.
    assert getattr(config, "_engine_factory", None) is None


def test_harness_config_engine_factory_field_is_keyword_only_friendly() -> None:
    """``HarnessConfig`` must keep the existing positional defaults working;
    the new factory hook is keyword-only-friendly (callers pass it by name)."""
    config = HarnessConfig(working_dir=Path("/tmp"), default_model="m")
    assert config.working_dir == Path("/tmp")
    assert config.default_model == "m"


# --- public API stability ----------------------------------------------------


def test_no_new_public_exports_added_by_slice_d() -> None:
    """Slice D adds NO public symbols. ``Session`` / ``SessionOptions`` /
    ``SessionCost`` / ``Harness`` / ``HarnessConfig`` are already public and
    the engine machinery stays private (``dream.engine._engine``)."""
    import dream

    public = set(dream.__all__)
    assert "QueryEngine" not in public
    assert "build_query_engine" not in public
    # All five facade types stay listed.
    assert {
        "Session",
        "SessionOptions",
        "SessionCost",
        "Harness",
        "HarnessConfig",
    } <= public
