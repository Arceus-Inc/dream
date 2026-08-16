"""Tests for the inline subagent executor's capability minimization.

The executor is the *live* v1 path (the spawn tool calls ``run_subagent_inline``
→ ``_build_subagent_manifest``). Capability minimization must be enforced here,
not only in the (currently non-live) projection — a subagent's tools are
``agent.tools ∩ parent_tools`` (§05: narrower-wins, can only drop).
"""

from __future__ import annotations

from dream.subagents._declaration import Subagent
from dream.subagents._inline_executor import _build_subagent_manifest
from dream.subagents._projection import intersect_tools


class TestIntersectTools:
    def test_drops_tools_not_in_parent(self) -> None:
        out = intersect_tools(
            ("read_file", "grep", "bash", "nuke_everything"),
            frozenset({"read_file", "grep"}),
        )
        assert out == ("read_file", "grep")  # order preserved, extras dropped

    def test_none_parent_keeps_all(self) -> None:
        # None = parent had no role restriction (full surface) → no filtering.
        out = intersect_tools(("read_file", "bash"), None)
        assert out == ("read_file", "bash")

    def test_empty_parent_drops_everything(self) -> None:
        assert intersect_tools(("read_file",), frozenset()) == ()


class TestBuildSubagentManifestMinimization:
    def test_manifest_tools_intersected_with_parent(self) -> None:
        """A subagent declaring tools beyond the parent's gets them dropped."""
        agent = Subagent(
            name="rogue",
            description="declares more than the parent has",
            tools=("read_file", "grep", "bash"),
        )
        manifest = _build_subagent_manifest(agent, parent_tools=frozenset({"read_file", "grep"}))
        assert manifest.tools == ("read_file", "grep")
        assert "bash" not in manifest.tools

    def test_manifest_tools_unrestricted_when_parent_none(self) -> None:
        agent = Subagent(name="x", description="d", tools=("read_file", "bash"))
        manifest = _build_subagent_manifest(agent, parent_tools=None)
        assert manifest.tools == ("read_file", "bash")

    def test_leaf_disallows_spawn(self) -> None:
        """A leaf subagent (no ``spawnable``) can never spawn — unchanged from v1."""
        agent = Subagent(name="x", description="d", tools=("read_file",))
        manifest = _build_subagent_manifest(agent, parent_tools=None)
        assert "spawn_subagent" in manifest.disallowed_tools


class TestSpawnEligibility:
    """Depth-2: a subagent with ``spawnable`` below the depth cap may itself spawn."""

    def _spawner(self, depth: int) -> Subagent:
        child = Subagent(name="web_research", description="reads the web", tools=("web_search",))
        return Subagent(
            name="strategist",
            description="frames the bet",
            tools=("read_file", "spawn_subagent"),
            spawnable=(child,),
            depth=depth,
        )

    def test_eligible_child_keeps_spawn_subagent(self) -> None:
        manifest = _build_subagent_manifest(self._spawner(depth=1), parent_tools=None)
        assert "spawn_subagent" not in manifest.disallowed_tools
        assert "spawn_subagent" in manifest.tools

    def test_child_at_depth_cap_is_a_leaf(self) -> None:
        """At MAX_SUBAGENT_DEPTH, even a declared spawner cannot spawn (grandchild is a leaf)."""
        manifest = _build_subagent_manifest(self._spawner(depth=2), parent_tools=None)
        assert "spawn_subagent" in manifest.disallowed_tools

    def test_no_spawnable_stays_leaf_even_below_cap(self) -> None:
        agent = Subagent(name="x", description="d", tools=("read_file", "spawn_subagent"), depth=1)
        manifest = _build_subagent_manifest(agent, parent_tools=None)
        assert "spawn_subagent" in manifest.disallowed_tools
