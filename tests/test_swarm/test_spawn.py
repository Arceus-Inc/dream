"""Tests for ``TeammateSpawnConfig`` and ``SpawnResult`` value objects."""

from __future__ import annotations

import pytest

from dream.swarm._spawn import (
    MAX_SUBAGENT_DEPTH,
    SpawnResult,
    TeammateSpawnConfig,
)


class TestSpawnConfigDefaults:
    def _cfg(self, **kw: object) -> TeammateSpawnConfig:
        params: dict[str, object] = {
            "name": "planner",
            "team": "alpha",
            "prompt": "go",
            "cwd": "/tmp",
            "parent_session_id": "sess-1",
        }
        params.update(kw)
        return TeammateSpawnConfig(**params)  # type: ignore[arg-type]

    def test_allow_permission_prompts_defaults_to_false(self) -> None:
        """Spec criterion #16: spawned roles auto-deny unlisted tools."""
        cfg = self._cfg()
        assert cfg.allow_permission_prompts is False

    def test_default_task_type_is_local_agent(self) -> None:
        cfg = self._cfg()
        assert cfg.task_type == "local_agent"

    def test_default_depth_is_one(self) -> None:
        """Depth 0 is the top-level runner; a freshly-built spawn config
        defaults to depth 1 (the role layer)."""
        cfg = self._cfg()
        assert cfg.depth == 1

    def test_permissions_is_tuple_immutable(self) -> None:
        cfg = self._cfg(permissions=["read", "git"])
        assert cfg.permissions == ("read", "git")
        # frozen dataclass: cannot mutate
        with pytest.raises(Exception):
            cfg.permissions = ()  # type: ignore[misc]

    def test_subscriptions_is_tuple_immutable(self) -> None:
        cfg = self._cfg(subscriptions=["topic.a", "topic.b"])
        assert cfg.subscriptions == ("topic.a", "topic.b")

    def test_rejects_string_permissions(self) -> None:
        # A bare string would silently coerce to ('r','e','a','d'); reject it.
        with pytest.raises(TypeError, match="permissions"):
            self._cfg(permissions="read")

    def test_rejects_string_subscriptions(self) -> None:
        with pytest.raises(TypeError, match="subscriptions"):
            self._cfg(subscriptions="topic.a")

    def test_rejects_depth_below_one(self) -> None:
        with pytest.raises(ValueError, match="depth"):
            self._cfg(depth=0)

    def test_max_depth_constant_is_three(self) -> None:
        # spec decision #9
        assert MAX_SUBAGENT_DEPTH == 3


class TestSpawnResult:
    def test_success_result(self) -> None:
        r = SpawnResult(task_id="t1", agent_id="a@b", backend_type="in_process")
        assert r.success is True
        assert r.error is None

    def test_failure_result_carries_error(self) -> None:
        r = SpawnResult(
            task_id="",
            agent_id="a@b",
            backend_type="in_process",
            success=False,
            error="boom",
        )
        assert r.success is False
        assert r.error == "boom"
