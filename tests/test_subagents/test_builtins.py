"""Harness builtin catalog: explore / plan / verify."""

from __future__ import annotations

from dream.subagents import (
    EXPLORE,
    GENERAL_PURPOSE,
    IsolationMode,
    PLAN,
    Subagent,
    SubagentSet,
    VERIFY,
    builtin_agents,
    merge_builtins,
)
from dream.subagents._builtins import spawn_catalog_names
from dream.subagents._host_blocklist import READONLY_DENIED_TOOLS
from dream.tools.builtin.spawn_subagent import resolve_agent, spawn_type_names


class TestBuiltins:
    def test_builtin_names(self) -> None:
        names = {agent.name for agent in builtin_agents()}
        assert names == {EXPLORE, PLAN, VERIFY}

    def test_explore_is_read_only(self) -> None:
        explore = next(agent for agent in builtin_agents() if agent.name == EXPLORE)
        assert explore.isolation is IsolationMode.SHARED
        assert not READONLY_DENIED_TOOLS.intersection(explore.tools)

    def test_verify_is_strict(self) -> None:
        verify = next(agent for agent in builtin_agents() if agent.name == VERIFY)
        assert verify.strict is True
        assert verify.output_schema is not None

    def test_merge_role_wins_on_collision(self) -> None:
        custom = Subagent(
            name=EXPLORE,
            description="role override",
            tools=("read_file",),
        )
        merged = merge_builtins(SubagentSet(agents={EXPLORE: custom}))
        assert merged.get(EXPLORE) is not None
        assert merged.get(EXPLORE).description == "role override"  # type: ignore[union-attr]

    def test_catalog_enum_order(self) -> None:
        names = spawn_catalog_names(None)
        assert names[:4] == (GENERAL_PURPOSE, EXPLORE, PLAN, VERIFY)

    def test_spawn_type_names_includes_builtins(self) -> None:
        assert EXPLORE in spawn_type_names(None)
        assert PLAN in spawn_type_names(SubagentSet())

    def test_resolve_explore(self) -> None:
        agent = resolve_agent(
            EXPLORE,
            subagent_set=None,
            parent_tools=frozenset({"read_file", "grep", "write_file"}),
            parent_name=None,
        )
        assert agent is not None
        assert agent.name == EXPLORE

    def test_spawned_by_enforced(self) -> None:
        gated = Subagent(
            name="nested_only",
            description="tier-2",
            tools=("read_file",),
            spawned_by=("orchestrator",),
        )
        agent = resolve_agent(
            "nested_only",
            subagent_set=SubagentSet(agents={"nested_only": gated}),
            parent_tools=frozenset({"read_file"}),
            parent_name=None,
        )
        assert agent is None
        agent = resolve_agent(
            "nested_only",
            subagent_set=SubagentSet(agents={"nested_only": gated}),
            parent_tools=frozenset({"read_file"}),
            parent_name="orchestrator",
        )
        assert agent is not None
