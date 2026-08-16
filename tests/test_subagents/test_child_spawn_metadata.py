"""Depth-2: the child session metadata an eligible spawner is handed.

``run_subagent_inline`` seeds a spawn-eligible child's ``SessionOptions.metadata`` with a *scoped*
subagent set (its declared ``spawnable``, one depth deeper, tool-intersected) and the *parent's*
spawn counter (so the per-beat cap spans the whole tree). A leaf gets nothing — unchanged. The
seeding is a pure helper so it can be tested without a live session.
"""

from __future__ import annotations

from dream.subagents._declaration import Subagent
from dream.subagents._inline_executor import build_child_spawn_metadata
from dream.subagents._projection import SubagentSet
from dream.tools.builtin.spawn_subagent import (
    HARNESS_KEY,
    PARENT_TOOLS_KEY,
    SPAWN_COUNT_KEY,
    SUBAGENT_SET_CONTEXT_KEY,
)


def _spawner(depth: int = 1) -> Subagent:
    child = Subagent(name="web_research", description="reads the web", tools=("web_search",))
    return Subagent(
        name="strategist",
        description="frames the bet",
        tools=("read_file", "spawn_subagent"),
        spawnable=(child,),
        depth=depth,
    )


class TestChildSpawnMetadata:
    def test_leaf_gets_no_spawn_metadata(self) -> None:
        leaf = Subagent(name="x", description="d", tools=("read_file",))
        meta = build_child_spawn_metadata(
            leaf, counter=[0], harness=object(), tracer=None, parent_tools=None
        )
        assert meta == {}

    def test_eligible_child_carries_the_same_counter_object(self) -> None:
        shared = [4]
        meta = build_child_spawn_metadata(
            _spawner(), counter=shared, harness=object(), tracer=None, parent_tools=None
        )
        assert meta[SPAWN_COUNT_KEY] is shared  # same object → cap spans the tree

    def test_scoped_set_holds_only_declared_spawnable_one_depth_deeper(self) -> None:
        meta = build_child_spawn_metadata(
            _spawner(depth=1), counter=[0], harness=object(), tracer=None, parent_tools=None
        )
        scoped: SubagentSet = meta[SUBAGENT_SET_CONTEXT_KEY]
        assert scoped.names() == ["web_research"]
        assert scoped.get("web_research").depth == 2  # grandchild sits at the cap → a leaf

    def test_grandchild_tools_intersected_with_child(self) -> None:
        """A spawnable child can only narrow: its tools ∩ the spawner's effective tools."""
        greedy = Subagent(name="web_research", description="d", tools=("web_search", "bash"))
        spawner = Subagent(
            name="strategist",
            description="d",
            tools=("read_file", "spawn_subagent", "web_search"),  # no bash
            spawnable=(greedy,),
            depth=1,
        )
        meta = build_child_spawn_metadata(
            spawner,
            counter=[0],
            harness=object(),
            tracer=None,
            parent_tools=frozenset({"read_file", "spawn_subagent", "web_search"}),
        )
        scoped: SubagentSet = meta[SUBAGENT_SET_CONTEXT_KEY]
        assert scoped.get("web_research").tools == ("web_search",)  # bash dropped

    def test_harness_and_parent_tools_wired(self) -> None:
        sentinel = object()
        meta = build_child_spawn_metadata(
            _spawner(),
            counter=[0],
            harness=sentinel,
            tracer=None,
            parent_tools=frozenset({"read_file", "spawn_subagent"}),
        )
        assert meta[HARNESS_KEY] is sentinel
        assert meta[PARENT_TOOLS_KEY] == frozenset({"read_file", "spawn_subagent"})
