"""Depth-2 integration: delegated sessions seed a spawn-eligible child's session.

The unit tests prove the pieces (manifest keeps spawn_subagent; build_child_spawn_metadata builds
the scoped set + shared counter). This closes the assembled gap: the delegated session threads
that metadata into the ``SessionOptions`` handed to ``harness.run_role`` — so a real child session
WOULD carry web_research + the shared counter and could dispatch it. Deterministic (fake harness),
so it isolates plumbing from a live model's choice to spawn or not.
"""

from __future__ import annotations

import asyncio

from dream.runner._role_session import RunRoleResult
from dream.session import SessionCost, SessionOptions
from dream.subagents._declaration import Subagent
from dream.subagents._inline_executor import run_subagent_session
from dream.tools.builtin.spawn_subagent import (
    HARNESS_KEY,
    SPAWN_COUNT_KEY,
    SUBAGENT_SET_CONTEXT_KEY,
)


class _CapturingHarness:
    """A fake Harness that records the SessionOptions run_role was called with."""

    def __init__(self) -> None:
        self.captured: SessionOptions | None = None

    async def run_role(self, manifest, intent, *, options=None, **_kw):  # type: ignore[no-untyped-def]
        self.captured = options
        return RunRoleResult(
            role="subagent",
            session_id="child-sid",
            final_text="done",
            cost=SessionCost(),
            events=(),
        )


def _spawner() -> Subagent:
    child = Subagent(name="web_research", description="reads the web", tools=("web_search",))
    return Subagent(
        name="strategist",
        description="frames the bet",
        tools=("read_file", "spawn_subagent"),
        spawnable=(child,),
        depth=1,
    )


def test_eligible_childs_session_carries_scoped_set_and_shared_counter() -> None:
    harness = _CapturingHarness()
    shared = [2]

    asyncio.run(
        run_subagent_session(
            _spawner(),
            prompt="frame it",
            harness=harness,  # type: ignore[arg-type]
            parent_tools=frozenset({"read_file", "spawn_subagent", "web_search"}),
            spawn_counter=shared,
            tracer=None,
        )
    )

    assert harness.captured is not None
    meta = harness.captured.metadata
    # The child session can see web_research (scoped) and shares the parent's per-beat counter.
    scoped = meta[SUBAGENT_SET_CONTEXT_KEY]
    assert scoped.names() == ["web_research"]
    assert scoped.get("web_research").depth == 2  # grandchild at the cap → a leaf
    assert meta[SPAWN_COUNT_KEY] is shared
    assert HARNESS_KEY in meta


def test_leaf_childs_session_gets_no_spawn_context() -> None:
    harness = _CapturingHarness()
    leaf = Subagent(name="reviewer", description="reviews", tools=("read_file",))

    asyncio.run(
        run_subagent_session(
            leaf, prompt="review", harness=harness,  # type: ignore[arg-type]
            parent_tools=None, spawn_counter=[0], tracer=None,
        )
    )

    assert harness.captured is not None
    assert SUBAGENT_SET_CONTEXT_KEY not in harness.captured.metadata  # leaf can't spawn — unchanged


def test_run_subagent_session_forwards_observer_to_child() -> None:
    """The parent observer is threaded into the child session's run_role, so the child's events
    (including a nested spawn) reach the same observer/bus."""

    class _RecordingHarness(_CapturingHarness):
        def __init__(self) -> None:
            super().__init__()
            self.observer = "unset"

        async def run_role(self, manifest, intent, *, options=None, observer=None, **_kw):  # type: ignore[no-untyped-def]
            self.observer = observer
            return await super().run_role(manifest, intent, options=options)

    class _Obs:
        def on_event(self, event: dict) -> None: ...

    harness = _RecordingHarness()
    obs = _Obs()
    asyncio.run(
        run_subagent_session(
            _spawner(), prompt="frame it", harness=harness,  # type: ignore[arg-type]
            parent_tools=None, spawn_counter=[0], tracer=None, observer=obs,
        )
    )
    assert harness.observer is obs
