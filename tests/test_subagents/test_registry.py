"""Tests for the Tier-2 SubagentRegistry."""

from __future__ import annotations

import pytest

from dream.subagents._declaration import Subagent
from dream.subagents._registry import SubagentRegistry


class TestSubagentRegistry:
    def test_register_and_get(self) -> None:
        reg = SubagentRegistry()
        agent = Subagent(name="researcher", description="Researches", tools=("grep",))
        reg.register(agent)
        assert reg.get("researcher") is agent
        assert "researcher" in reg
        assert len(reg) == 1

    def test_duplicate_raises(self) -> None:
        reg = SubagentRegistry()
        agent = Subagent(name="researcher", description="Researches", tools=("grep",))
        reg.register(agent)
        with pytest.raises(ValueError, match="already registered"):
            reg.register(agent)

    def test_get_missing(self) -> None:
        reg = SubagentRegistry()
        assert reg.get("missing") is None
        assert "missing" not in reg

    def test_list_names(self) -> None:
        reg = SubagentRegistry()
        reg.register(Subagent(name="a", description="A", tools=("x",)))
        reg.register(Subagent(name="b", description="B", tools=("y",)))
        assert sorted(reg.list_names()) == ["a", "b"]

    def test_resolve(self) -> None:
        reg = SubagentRegistry()
        a = Subagent(name="a", description="A", tools=("x",))
        b = Subagent(name="b", description="B", tools=("y",))
        reg.register(a)
        reg.register(b)
        resolved = reg.resolve(("a", "b"))
        assert resolved == [a, b]

    def test_resolve_missing_raises(self) -> None:
        reg = SubagentRegistry()
        reg.register(Subagent(name="a", description="A", tools=("x",)))
        with pytest.raises(KeyError, match="not found"):
            reg.resolve(("a", "missing"))
