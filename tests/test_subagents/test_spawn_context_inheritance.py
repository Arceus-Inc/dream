"""Depth-2 enabling change: the engine factory prefers an INCOMING spawn context.

For a child subagent to spawn its own (Tier-2) subagent, its session must carry a *scoped*
subagent set and the *parent's* spawn counter — passed via ``SessionOptions.metadata``. The
factory (``_build_session_engine``) normally seeds these fresh from harness state; when they are
present on ``options.metadata`` it must prefer them, so the child inherits a scoped set and the
per-beat cap spans the whole tree. The top-level (parent) path — no incoming keys — is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from dream.repl._session import build_default_harness
from dream.session import SessionOptions
from dream.subagents._declaration import Subagent, SubagentSet
from dream.tools.builtin.spawn_subagent import (
    SPAWN_COUNT_KEY,
    SUBAGENT_SET_CONTEXT_KEY,
)


def _env() -> dict[str, str]:
    return {
        "DREAM_SMOKE_API_KEY": "sk-test",
        "DREAM_SMOKE_MODEL": "gpt-test",
        "DREAM_SMOKE_BASE_URL": "http://127.0.0.1:9/v1",
    }


def _child_set() -> SubagentSet:
    return SubagentSet(
        agents={
            "web_research": Subagent(
                name="web_research", description="reads the web", tools=("web_search",)
            )
        }
    )


def test_factory_prefers_incoming_spawn_counter(tmp_path: Path) -> None:
    """A counter passed on options.metadata is the SAME object the dispatcher carries —
    so a child's spawns increment the parent's per-beat counter (cap spans the tree)."""
    shared_counter = [3]
    options = SessionOptions(
        metadata={
            SPAWN_COUNT_KEY: shared_counter,
            SUBAGENT_SET_CONTEXT_KEY: _child_set(),
        }
    )
    harness = build_default_harness(env=_env(), working_dir=tmp_path)
    factory = harness.config._engine_factory
    assert factory is not None
    engine = factory("child-sid", options)

    assert engine.dispatcher.context_metadata[SPAWN_COUNT_KEY] is shared_counter


def test_factory_prefers_incoming_scoped_subagent_set(tmp_path: Path) -> None:
    """A scoped set passed on options.metadata reaches the child tool context verbatim —
    so a child can only spawn what it declared, not the parent's full roster."""
    child_set = _child_set()
    options = SessionOptions(
        metadata={
            SPAWN_COUNT_KEY: [0],
            SUBAGENT_SET_CONTEXT_KEY: child_set,
        }
    )
    harness = build_default_harness(env=_env(), working_dir=tmp_path)
    factory = harness.config._engine_factory
    assert factory is not None
    engine = factory("child-sid", options)

    assert engine.dispatcher.context_metadata[SUBAGENT_SET_CONTEXT_KEY] is child_set


def test_top_level_session_seeds_fresh_counter(tmp_path: Path) -> None:
    """No incoming spawn keys ⇒ unchanged behaviour: the parent path is not affected."""
    harness = build_default_harness(env=_env(), working_dir=tmp_path)
    factory = harness.config._engine_factory
    assert factory is not None
    engine = factory("parent-sid", SessionOptions())

    # No subagents configured on this bare harness ⇒ no spawn context wired at all.
    assert SUBAGENT_SET_CONTEXT_KEY not in engine.dispatcher.context_metadata
