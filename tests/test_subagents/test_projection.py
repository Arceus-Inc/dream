"""Tests for the chorus → dream projection (Subagent → TeammateSpawnConfig)."""

from __future__ import annotations

from dream.subagents._declaration import Subagent
from dream.subagents._projection import build_subagent_set, project_subagent


class TestProjectSubagent:
    def test_basic_projection(self) -> None:
        agent = Subagent(
            name="reviewer",
            description="Reviews code changes",
            tools=("read_file", "grep"),
            depth=1,
        )
        config = project_subagent(
            agent,
            parent_session_id="sess-123",
            parent_tools=frozenset({"read_file", "grep", "bash", "git"}),
            parent_permissions=("read", "write", "execute"),
            team="eng-team",
            cwd="/workspace",
            prompt="Review this diff for correctness",
        )
        assert config.name == "reviewer"
        assert config.team == "eng-team"
        assert config.prompt == "Review this diff for correctness"
        assert config.cwd == "/workspace"
        assert config.parent_session_id == "sess-123"
        assert config.depth == 1
        assert config.allow_permission_prompts is False
        assert config.task_type == "in_process_teammate"

    def test_capability_minimization_tools(self) -> None:
        """Tools are intersected with parent — can only drop, never widen."""
        agent = Subagent(
            name="test",
            description="test",
            tools=("read_file", "grep", "bash", "nuke_everything"),
        )
        config = project_subagent(
            agent,
            parent_session_id="s",
            parent_tools=frozenset({"read_file", "grep"}),
            parent_permissions=(),
            team="t",
            cwd="/",
            prompt="go",
        )
        # "bash" and "nuke_everything" are NOT in parent_tools, so dropped
        assert config.permissions == ()

    def test_permission_overlay_tightens(self) -> None:
        """Permission overlay removes tokens from parent — never widens."""
        agent = Subagent(
            name="readonly",
            description="read only",
            tools=("read_file",),
            permission_overlay=("write", "execute"),
        )
        config = project_subagent(
            agent,
            parent_session_id="s",
            parent_tools=frozenset({"read_file"}),
            parent_permissions=("read", "write", "execute"),
            team="t",
            cwd="/",
            prompt="go",
        )
        assert config.permissions == ("read",)

    def test_custom_model(self) -> None:
        agent = Subagent(
            name="cheap",
            description="cheap",
            tools=("read_file",),
            model="gpt-4o-mini",
        )
        config = project_subagent(
            agent,
            parent_session_id="s",
            parent_tools=frozenset({"read_file"}),
            parent_permissions=(),
            team="t",
            cwd="/",
            prompt="go",
        )
        assert config.model == "gpt-4o-mini"

    def test_custom_system_prompt(self) -> None:
        agent = Subagent(
            name="custom",
            description="custom",
            tools=("read_file",),
            system_prompt="You are a specialist.",
        )
        config = project_subagent(
            agent,
            parent_session_id="s",
            parent_tools=frozenset({"read_file"}),
            parent_permissions=(),
            team="t",
            cwd="/",
            prompt="go",
        )
        assert config.system_prompt == "You are a specialist."
        assert config.system_prompt_mode == "replace"

    def test_default_system_prompt_generated(self) -> None:
        agent = Subagent(
            name="reviewer",
            description="Reviews code",
            tools=("read_file",),
        )
        config = project_subagent(
            agent,
            parent_session_id="s",
            parent_tools=frozenset({"read_file"}),
            parent_permissions=(),
            team="t",
            cwd="/",
            prompt="go",
        )
        assert "reviewer" in config.system_prompt
        assert "Reviews code" in config.system_prompt


class TestBuildSubagentSet:
    def test_merge_tiers(self) -> None:
        tier1 = [Subagent(name="reviewer", description="R", tools=("read_file",))]
        tier2 = [Subagent(name="researcher", description="Q", tools=("grep",))]
        s = build_subagent_set(tier1_agents=tier1, tier2_agents=tier2)
        assert "reviewer" in s
        assert "researcher" in s
        assert len(s) == 2

    def test_duplicate_name_raises(self) -> None:
        tier1 = [Subagent(name="dup", description="1", tools=("x",))]
        tier2 = [Subagent(name="dup", description="2", tools=("y",))]
        import pytest

        with pytest.raises(ValueError, match="Duplicate"):
            build_subagent_set(tier1_agents=tier1, tier2_agents=tier2)

    def test_empty_builds_empty_set(self) -> None:
        s = build_subagent_set()
        assert len(s) == 0
        assert not s
