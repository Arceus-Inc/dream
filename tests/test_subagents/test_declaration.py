"""Tests for the Subagent declaration data model."""

from __future__ import annotations

import pytest

from dream.subagents import IsolationMode
from dream.subagents._declaration import Subagent, SubagentSet


class TestSubagentDeclaration:
    def test_basic_construction(self) -> None:
        agent = Subagent(
            name="reviewer",
            description="Reviews code changes for quality",
            tools=("read_file", "grep"),
        )
        assert agent.name == "reviewer"
        assert agent.description == "Reviews code changes for quality"
        assert agent.tools == ("read_file", "grep")
        assert agent.depth == 1
        assert agent.model is None
        assert agent.skills == ()
        assert agent.permission_overlay == ()
        assert agent.max_turns == 8

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            Subagent(name="", description="test", tools=("x",))

    def test_empty_description_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            Subagent(name="test", description="", tools=("x",))

    def test_bare_string_tools_raises(self) -> None:
        with pytest.raises(TypeError, match="sequence of strings"):
            Subagent(name="test", description="test", tools="read_file")  # type: ignore[arg-type]

    def test_depth_below_1_raises(self) -> None:
        with pytest.raises(ValueError, match="depth must be >= 1"):
            Subagent(name="test", description="test", tools=("x",), depth=0)

    def test_frozen(self) -> None:
        agent = Subagent(name="test", description="test", tools=("x",))
        with pytest.raises(AttributeError):
            agent.name = "other"  # type: ignore[misc]

    def test_round_trip_dict(self) -> None:
        agent = Subagent(
            name="query_orchestrator",
            description="Orchestrates SQL queries",
            tools=("bash", "read_file"),
            skills=("sql_expert",),
            permission_overlay=("write",),
            depth=1,
            model="gpt-4o-mini",
            spawned_by=("analyst",),
            system_prompt="You are a SQL expert.",
            max_turns=4,
            isolation=IsolationMode.WORKTREE,
        )
        d = agent.to_dict()
        restored = Subagent.from_dict(d)
        assert restored == agent
        assert restored.isolation is IsolationMode.WORKTREE

    def test_from_dict_defaults(self) -> None:
        d = {"name": "test", "description": "test", "tools": ["x"]}
        agent = Subagent.from_dict(d)
        assert agent.depth == 1
        assert agent.model is None
        assert agent.max_turns == 8


class TestSubagentSet:
    def test_empty_set(self) -> None:
        s = SubagentSet()
        assert len(s) == 0
        assert not s
        assert "reviewer" not in s

    def test_non_empty_set(self) -> None:
        agents = {
            "reviewer": Subagent(name="reviewer", description="Reviews", tools=("read_file",)),
            "researcher": Subagent(name="researcher", description="Researches", tools=("grep",)),
        }
        s = SubagentSet(agents=agents)
        assert len(s) == 2
        assert s
        assert "reviewer" in s
        assert "unknown" not in s
        assert s.get("reviewer") is agents["reviewer"]
        assert s.get("unknown") is None
        assert sorted(s.names()) == ["researcher", "reviewer"]

    def test_iterates_agents(self) -> None:
        agents = {
            "reviewer": Subagent(
                name="reviewer", description="Code reviewer", tools=("read_file",)
            ),
        }
        s = SubagentSet(agents=agents)
        assert [agent.name for agent in s] == ["reviewer"]


class TestSpawnable:
    """Depth-2: a subagent may declare the Tier-2 agents it can itself dispatch."""

    def test_spawnable_defaults_empty(self) -> None:
        agent = Subagent(name="strategist", description="frames the bet", tools=("read_file",))
        assert agent.spawnable == ()

    def test_carries_declared_spawnable(self) -> None:
        child = Subagent(name="web_research", description="reads the web", tools=("web_search",))
        parent = Subagent(
            name="strategist",
            description="frames the bet",
            tools=("read_file", "spawn_subagent"),
            spawnable=(child,),
        )
        assert parent.spawnable == (child,)

    def test_spawnable_round_trips(self) -> None:
        child = Subagent(name="web_research", description="reads the web", tools=("web_search",))
        parent = Subagent(
            name="strategist",
            description="frames the bet",
            tools=("read_file", "spawn_subagent"),
            spawnable=(child,),
        )
        restored = Subagent.from_dict(parent.to_dict())
        assert restored.spawnable == (child,)

    def test_max_subagent_depth_is_two(self) -> None:
        from dream.subagents._declaration import MAX_SUBAGENT_DEPTH

        assert MAX_SUBAGENT_DEPTH == 2
