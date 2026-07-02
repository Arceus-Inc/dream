"""Spec 10 slice G2 — ``Harness.run_role`` primitive.

The missing piece that lets the production planner / generator /
evaluator heads open a role-bound session and hand the final assistant
text + cost back to ``dream.runner.run_task``.

What this slice pins:

- The role is resolved from a name (bundled default), from an explicit
  ``RoleManifest``, or — when ``harness_dir`` is set — from the layered
  loader.
- The manifest's system prompt is combined with the caller's; the
  resolved manifest lands on ``SessionOptions.metadata`` so a role-aware
  engine factory can intersect tools / pick permission mode.
- The session is drained to completion: ``TextDelta`` events are
  concatenated into ``final_text``, cost is captured, and the session is
  closed even on the error path.
- An ``ErrorEvent`` mid-stream becomes a ``RoleSessionError``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from dream.engine._cost import UsageSnapshot
from dream.engine._engine import QueryEngine
from dream.engine._events import (
    AssistantTurnComplete,
    ErrorEvent,
    StreamEvent,
)
from dream.engine._messages import ConversationMessage
from dream.events import TextDelta
from dream.harness import Harness, HarnessConfig
from dream.roles import RoleManifest, default_role_manifest
from dream.runner import RoleSessionError, RunRoleResult
from dream.runner._role_session import (
    ROLE_MANIFEST_METADATA_KEY,
    ROLE_NAME_METADATA_KEY,
    resolve_role_manifest,
)
from dream.session import SessionOptions
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn


def _capture_factory(
    captured: list[SessionOptions],
    *,
    streamer: FakeStreamer | None = None,
):
    """Engine factory that captures ``SessionOptions`` on every call."""
    streamer = streamer or FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        captured.append(options)
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    return _factory


def _harness(
    *, streamer: FakeStreamer | None = None
) -> tuple[Harness, list[SessionOptions]]:
    captured: list[SessionOptions] = []
    config = HarnessConfig(_engine_factory=_capture_factory(captured, streamer=streamer))  # type: ignore[call-arg]
    return Harness(config), captured


# --- resolve_role_manifest --------------------------------------------------


def test_resolve_role_manifest_returns_explicit_manifest_unchanged() -> None:
    m = default_role_manifest("planner")
    assert resolve_role_manifest(m) is m


def test_resolve_role_manifest_returns_default_for_known_name() -> None:
    m = resolve_role_manifest("planner")
    assert m.name == "planner"


def test_resolve_role_manifest_uses_harness_dir_overlay_when_given(
    tmp_path: Path,
) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "planner.toml").write_text(
        'description = "overlay planner"\n', encoding="utf-8"
    )

    m = resolve_role_manifest("planner", harness_dir=tmp_path)

    assert m.description == "overlay planner"
    assert m.name == "planner"


def test_resolve_role_manifest_falls_back_to_default_when_overlay_missing(
    tmp_path: Path,
) -> None:
    # ``tmp_path`` has no roles/ subdir — loader silently returns bundled default.
    m = resolve_role_manifest("planner", harness_dir=tmp_path)
    assert m == default_role_manifest("planner")


def test_resolve_role_manifest_raises_on_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        resolve_role_manifest("unknown")  # type: ignore[arg-type]


# --- run_role: prompt + metadata wiring -------------------------------------


async def test_run_role_passes_manifest_prompt_to_factory() -> None:
    harness, captured = _harness()

    await harness.run_role("planner", "intent")

    assert len(captured) == 1
    opts = captured[0]
    planner = default_role_manifest("planner")
    assert opts.system_prompt is not None
    assert opts.system_prompt.startswith(planner.system_prompt)


async def test_run_role_appends_caller_system_prompt_after_manifest() -> None:
    harness, captured = _harness()

    await harness.run_role(
        "planner", "intent", options=SessionOptions(system_prompt="EXTRA")
    )

    planner = default_role_manifest("planner")
    opts = captured[0]
    assert opts.system_prompt == f"{planner.system_prompt}\n\nEXTRA"


async def test_run_role_records_manifest_on_session_options_metadata() -> None:
    harness, captured = _harness()

    await harness.run_role("planner", "intent")

    opts = captured[0]
    assert opts.metadata[ROLE_NAME_METADATA_KEY] == "planner"
    manifest = opts.metadata[ROLE_MANIFEST_METADATA_KEY]
    assert isinstance(manifest, RoleManifest)
    assert manifest.name == "planner"


async def test_run_role_preserves_caller_metadata() -> None:
    harness, captured = _harness()

    await harness.run_role(
        "planner",
        "intent",
        options=SessionOptions(metadata={"trace_id": "abc"}),
    )

    opts = captured[0]
    assert opts.metadata["trace_id"] == "abc"
    assert opts.metadata[ROLE_NAME_METADATA_KEY] == "planner"


async def test_run_role_forwards_model_and_max_turns_to_factory() -> None:
    harness, captured = _harness()

    await harness.run_role(
        "planner",
        "intent",
        options=SessionOptions(model="m", max_turns=7),
    )

    opts = captured[0]
    assert opts.model == "m"
    assert opts.max_turns == 7


async def test_run_role_does_not_mutate_caller_options() -> None:
    """Caller's ``SessionOptions`` is frozen, but the metadata dict isn't —
    the helper must copy before mutating."""
    harness, _ = _harness()
    caller_meta = {"trace_id": "abc"}
    opts = SessionOptions(metadata=caller_meta)

    await harness.run_role("planner", "intent", options=opts)

    assert caller_meta == {"trace_id": "abc"}, "caller metadata was mutated"


# --- run_role: result shape -------------------------------------------------


async def test_run_role_returns_concatenated_final_text() -> None:
    streamer = FakeStreamer(
        turns=[FakeTurn(text_chunks=["hel", "lo ", "world"])]
    )
    harness, _ = _harness(streamer=streamer)

    result = await harness.run_role("planner", "intent")

    assert isinstance(result, RunRoleResult)
    assert result.final_text == "hello world"
    assert result.role == "planner"


async def test_run_role_captures_session_cost() -> None:
    streamer = FakeStreamer(
        turns=[
            FakeTurn(
                text_chunks=["ok"],
                usage=UsageSnapshot(input_tokens=5, output_tokens=3),
            )
        ]
    )
    harness, _ = _harness(streamer=streamer)

    result = await harness.run_role("planner", "intent")

    assert result.cost.input_tokens == 5
    assert result.cost.output_tokens == 3


async def test_run_role_includes_text_delta_events_in_result() -> None:
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["a", "b"])])
    harness, _ = _harness(streamer=streamer)

    result = await harness.run_role("planner", "intent")

    text_deltas = [e for e in result.events if isinstance(e, TextDelta)]
    assert [d.text for d in text_deltas] == ["a", "b"]


async def test_run_role_returns_session_id() -> None:
    harness, _ = _harness()

    result = await harness.run_role("planner", "intent")

    assert isinstance(result.session_id, str)
    assert result.session_id


# --- run_role: explicit manifest -------------------------------------------


async def test_run_role_accepts_explicit_manifest_object() -> None:
    harness, captured = _harness()
    custom = default_role_manifest("planner").model_copy(
        update={"system_prompt": "CUSTOM PLANNER"}
    )

    await harness.run_role(custom, "intent")

    opts = captured[0]
    assert opts.system_prompt is not None
    assert opts.system_prompt.startswith("CUSTOM PLANNER")
    assert opts.metadata[ROLE_MANIFEST_METADATA_KEY] is custom


# --- run_role: lifecycle ---------------------------------------------------


async def test_run_role_closes_session_after_completion() -> None:
    captured_sessions: list[object] = []
    streamer = FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])])
    config = HarnessConfig(  # type: ignore[call-arg]
        _engine_factory=_capture_factory([], streamer=streamer)
    )
    harness = Harness(config)

    original = harness.start_session

    async def wrapper(*args, **kwargs):
        session = await original(*args, **kwargs)
        captured_sessions.append(session)
        return session

    harness.start_session = wrapper  # type: ignore[method-assign]

    await harness.run_role("planner", "intent")

    assert captured_sessions
    assert getattr(captured_sessions[0], "_closed") is True


# --- run_role: error path --------------------------------------------------


class _ErrorThenCompleteStreamer:
    """One-turn streamer that emits an ``ErrorEvent`` then completes the turn.

    The contract for ``TurnStreamer`` is that the iterator ends with one
    ``AssistantTurnComplete`` — emitting the error first then closing the
    turn keeps the loop well-formed while the public stream surfaces an
    ``events.Error``.
    """

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        yield ErrorEvent(message="kaboom")
        yield AssistantTurnComplete(blocks=[], usage=UsageSnapshot())


async def test_run_role_raises_role_session_error_on_engine_error() -> None:
    config = HarnessConfig(  # type: ignore[call-arg]
        _engine_factory=_capture_factory([], streamer=_ErrorThenCompleteStreamer()),  # type: ignore[arg-type]
    )
    harness = Harness(config)

    with pytest.raises(RoleSessionError, match="kaboom"):
        await harness.run_role("planner", "intent")


async def test_run_role_closes_session_even_when_error_raised() -> None:
    captured_sessions: list[object] = []
    config = HarnessConfig(  # type: ignore[call-arg]
        _engine_factory=_capture_factory([], streamer=_ErrorThenCompleteStreamer()),  # type: ignore[arg-type]
    )
    harness = Harness(config)

    original = harness.start_session

    async def wrapper(*args, **kwargs):
        session = await original(*args, **kwargs)
        captured_sessions.append(session)
        return session

    harness.start_session = wrapper  # type: ignore[method-assign]

    with pytest.raises(RoleSessionError):
        await harness.run_role("planner", "intent")

    assert captured_sessions
    assert getattr(captured_sessions[0], "_closed") is True


# --- run_role: missing engine factory --------------------------------------


async def test_run_role_raises_when_no_engine_factory_configured() -> None:
    harness = Harness(HarnessConfig())  # no factory hook
    with pytest.raises(NotImplementedError):
        await harness.run_role("planner", "intent")


# --- run_role: role.session.closed event shape (piece 2) -------------------


def _harness_with_model(model: str) -> Harness:
    """Harness whose engine factory always uses the given model id."""
    streamer = FakeStreamer(
        turns=[
            FakeTurn(
                text_chunks=["hi"],
                usage=UsageSnapshot(
                    input_tokens=10, output_tokens=5,
                    cache_read_tokens=2, cache_write_tokens=1,
                ),
            )
        ]
    )

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            model=model,
        )

    config = HarnessConfig(_engine_factory=_factory)  # type: ignore[call-arg]
    return Harness(config)


async def test_role_session_closed_event_includes_model() -> None:
    from dream.runner._observer import _CapturingObserver

    harness = _harness_with_model("claude-3-5-sonnet")
    observer = _CapturingObserver()

    await harness.run_role("planner", "intent", observer=observer)

    closed_events = [e for e in observer.events if e.get("kind") == "role.session.closed"]
    assert len(closed_events) == 1
    assert closed_events[0]["model"] == "claude-3-5-sonnet"


async def test_role_session_closed_event_includes_usage_dict() -> None:
    from dream.runner._observer import _CapturingObserver

    harness = _harness_with_model("gpt-4o")
    observer = _CapturingObserver()

    await harness.run_role("planner", "intent", observer=observer)

    closed_events = [e for e in observer.events if e.get("kind") == "role.session.closed"]
    assert len(closed_events) == 1
    ev = closed_events[0]
    usage = ev["usage"]
    assert isinstance(usage, dict)
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 5
    assert usage["cache_read_tokens"] == 2
    assert usage["cache_write_tokens"] == 1


async def test_role_session_closed_event_includes_cost_usd() -> None:
    from dream.runner._observer import _CapturingObserver

    harness = _harness_with_model("m")
    observer = _CapturingObserver()

    await harness.run_role("planner", "intent", observer=observer)

    closed_events = [e for e in observer.events if e.get("kind") == "role.session.closed"]
    assert len(closed_events) == 1
    ev = closed_events[0]
    assert "cost_usd" in ev
    assert ev["cost_usd"] == 0.0


async def test_role_session_closed_event_no_getattr_used() -> None:
    """Behavioural: all four token keys are present via direct field access."""
    from dream.runner._observer import _CapturingObserver

    harness = _harness_with_model("m")
    observer = _CapturingObserver()

    await harness.run_role("planner", "intent", observer=observer)

    closed_events = [e for e in observer.events if e.get("kind") == "role.session.closed"]
    ev = closed_events[0]
    # All four sub-keys must be present under "usage"
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        assert key in ev["usage"], f"missing key: {key}"


# --- observer propagation (depth-2 visibility) ------------------------------


async def test_run_role_stashes_observer_on_session_metadata() -> None:
    """An observer passed to run_role lands on options.metadata so a spawn tool can propagate it
    into child sessions — making nested spawns visible on the same observer/bus."""
    from dream.tools.builtin.spawn_subagent import OBSERVER_KEY

    class _Obs:
        def on_event(self, event: dict) -> None: ...

    harness, captured = _harness()
    obs = _Obs()

    await harness.run_role("planner", "intent", observer=obs)  # type: ignore[arg-type]

    assert captured[0].metadata[OBSERVER_KEY] is obs


async def test_run_role_without_observer_sets_no_observer_key() -> None:
    from dream.tools.builtin.spawn_subagent import OBSERVER_KEY

    harness, captured = _harness()
    await harness.run_role("planner", "intent")
    assert OBSERVER_KEY not in captured[0].metadata
