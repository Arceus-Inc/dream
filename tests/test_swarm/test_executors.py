"""Tests for the three teammate executors + depth cap + bridge refusal.

Each executor delivers worker results to the leader's mailbox as a
``task_notification`` (spec 10 §"Worker notification" / decision #11), so the
tests assert via the mailbox file shape from slice 10-B.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from dream.swarm._paths import leader_inbox_dir
from dream.swarm._registry import BackendRegistry
from dream.swarm._remote import RemoteExecutor
from dream.swarm._spawn import (
    MAX_SUBAGENT_DEPTH,
    BridgeDisabled,
    TeammateSpawnConfig,
)
from dream.swarm.in_process import InProcessExecutor
from dream.swarm.subprocess_backend import SubprocessExecutor
from dream.tasks._manager import BackgroundTaskManager

# --- helpers -------------------------------------------------------------


def _cfg(
    *,
    name: str = "researcher",
    team: str = "alpha",
    depth: int = 1,
    task_type: str = "local_agent",
    worktree_path: str | None = None,
) -> TeammateSpawnConfig:
    return TeammateSpawnConfig(
        name=name,
        team=team,
        prompt="go",
        cwd=str(Path.cwd()),
        parent_session_id="sess-1",
        depth=depth,
        task_type=task_type,  # type: ignore[arg-type]
        worktree_path=worktree_path,
    )


def _drain_notifications(inbox: Path) -> list[dict[str, Any]]:
    if not inbox.exists():
        return []
    files = sorted(inbox.glob("*.json"))
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


# --- InProcessExecutor ---------------------------------------------------


class TestInProcessExecutor:
    async def test_spawn_runs_factory_and_returns_task_id(
        self, tmp_path: Path
    ) -> None:
        called_with: list[TeammateSpawnConfig] = []

        async def factory(cfg: TeammateSpawnConfig) -> str:
            called_with.append(cfg)
            return "all done"

        ex = InProcessExecutor(
            worktree_root=tmp_path,
            leader_id="leader-1",
            factory=factory,
        )
        res = await ex.spawn(_cfg(task_type="in_process_teammate"))
        await ex.wait_all()

        assert res.success is True
        assert res.backend_type == "in_process"
        assert res.agent_id == "researcher@alpha"
        assert called_with and called_with[0].name == "researcher"

    async def test_factory_completion_writes_task_notification_to_leader_inbox(
        self, tmp_path: Path
    ) -> None:
        async def factory(cfg: TeammateSpawnConfig) -> str:
            return "summary line"

        ex = InProcessExecutor(
            worktree_root=tmp_path,
            leader_id="leader-1",
            factory=factory,
        )
        res = await ex.spawn(_cfg(task_type="in_process_teammate"))
        await ex.wait_all()

        notifications = _drain_notifications(
            leader_inbox_dir(tmp_path, "leader-1")
        )
        assert len(notifications) == 1
        msg = notifications[0]
        # task-notification envelope shape pinned by 10-B factory
        assert msg["type"] == "task_notification"
        assert msg["sender"] == "researcher@alpha"
        assert msg["recipient"] == "leader-1"
        assert msg["payload"]["task_id"] == res.task_id
        assert msg["payload"]["status"] == "completed"
        assert msg["payload"]["summary"] == "summary line"

    async def test_factory_exception_writes_failed_notification(
        self, tmp_path: Path
    ) -> None:
        async def factory(cfg: TeammateSpawnConfig) -> str:
            raise RuntimeError("kaboom")

        ex = InProcessExecutor(
            worktree_root=tmp_path,
            leader_id="leader-1",
            factory=factory,
        )
        await ex.spawn(_cfg(task_type="in_process_teammate"))
        await ex.wait_all()

        notifications = _drain_notifications(
            leader_inbox_dir(tmp_path, "leader-1")
        )
        assert len(notifications) == 1
        assert notifications[0]["payload"]["status"] == "failed"
        assert "kaboom" in notifications[0]["payload"]["summary"]

    async def test_finished_tasks_are_evicted_from_tasks_map(
        self, tmp_path: Path
    ) -> None:
        """Long-running leaders spawn many teammates; finished runner tasks
        must not be retained in ``_tasks`` indefinitely (memory leak)."""
        async def factory(cfg: TeammateSpawnConfig) -> str:
            return "done"

        ex = InProcessExecutor(
            worktree_root=tmp_path,
            leader_id="leader-1",
            factory=factory,
        )
        for i in range(3):
            await ex.spawn(_cfg(name=f"worker-{i}", task_type="in_process_teammate"))
        await ex.wait_all()
        # Give the done-callbacks a turn to run after the tasks complete.
        await _sleep(0.0)
        assert ex._tasks == {}


# --- SubprocessExecutor --------------------------------------------------


class TestSubprocessExecutor:
    async def test_spawn_creates_background_task_and_runs_argv(
        self, tmp_path: Path
    ) -> None:
        # exit 0 quickly — we only verify the seam, not what dream.repl does
        argv = [sys.executable, "-c", "import sys; sys.exit(0)"]
        manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
        ex = SubprocessExecutor(
            worktree_root=tmp_path,
            leader_id="leader-1",
            task_manager=manager,
            argv_builder=lambda cfg: list(argv),
        )

        res = await ex.spawn(_cfg())
        # wait for the BackgroundTaskManager waiter to flip status
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            rec = manager.get_task(res.task_id)
            if rec is not None and rec.status in {"completed", "failed", "killed"}:
                break
            await _sleep(0.05)
        else:
            pytest.fail("subprocess task did not reach terminal status")

        assert res.success is True
        assert res.backend_type == "subprocess"
        assert res.agent_id == "researcher@alpha"

    async def test_completion_listener_writes_task_notification(
        self, tmp_path: Path
    ) -> None:
        """Decision #11: worker result reaches the leader via the
        BackgroundTaskManager completion-listener seam, not a sync return."""
        argv = [sys.executable, "-c", "import sys; sys.exit(0)"]
        manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
        ex = SubprocessExecutor(
            worktree_root=tmp_path,
            leader_id="leader-1",
            task_manager=manager,
            argv_builder=lambda cfg: list(argv),
        )
        res = await ex.spawn(_cfg())

        deadline = time.monotonic() + 5.0
        inbox = leader_inbox_dir(tmp_path, "leader-1")
        while time.monotonic() < deadline:
            if inbox.exists() and any(inbox.glob("*.json")):
                break
            await _sleep(0.05)
        else:
            pytest.fail("no task_notification delivered to leader inbox")

        msgs = _drain_notifications(inbox)
        assert len(msgs) == 1
        msg = msgs[0]
        assert msg["type"] == "task_notification"
        assert msg["sender"] == "researcher@alpha"
        assert msg["payload"]["task_id"] == res.task_id
        assert msg["payload"]["status"] == "completed"

    async def test_spawn_failure_propagates_as_unsuccessful_result(
        self, tmp_path: Path
    ) -> None:
        manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
        ex = SubprocessExecutor(
            worktree_root=tmp_path,
            leader_id="leader-1",
            task_manager=manager,
            argv_builder=lambda cfg: ["this-binary-does-not-exist-xyz"],
        )
        res = await ex.spawn(_cfg())
        assert res.success is False
        assert res.error  # non-empty

    async def test_successful_spawn_unregisters_listener_after_completion(
        self, tmp_path: Path
    ) -> None:
        """The completion listener is one-shot: once the spawned task reaches a
        terminal state it unregisters itself, so ``_listeners`` does not grow
        unbounded across many successful spawns."""
        argv = [sys.executable, "-c", "import sys; sys.exit(0)"]
        manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
        ex = SubprocessExecutor(
            worktree_root=tmp_path,
            leader_id="leader-1",
            task_manager=manager,
            argv_builder=lambda cfg: list(argv),
        )
        inbox = leader_inbox_dir(tmp_path, "leader-1")

        for _ in range(3):
            await ex.spawn(_cfg())
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if len(_drain_notifications(inbox)) > 0:
                    break
                await _sleep(0.05)
            # Drop delivered notifications so the next spawn's wait is clean.
            for f in inbox.glob("*.json"):
                f.unlink()

        # Each spawn's listener must have removed itself once its task finished.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and manager._listeners:
            await _sleep(0.05)
        assert manager._listeners == {}


# --- RemoteExecutor (bridge gated off) -----------------------------------


class TestRemoteExecutor:
    async def test_agent_id_is_sanitized(self, tmp_path: Path) -> None:
        """The returned ``agent_id`` must use the sanitizer contract so it
        matches the IDs used by the team registry / identity flows, not the
        raw ``name@team`` (which can carry spaces or ``@``)."""
        ex = RemoteExecutor(worktree_root=tmp_path, leader_id="leader-1")
        res = await ex.spawn(_cfg(name="Data Scout", team="Team Alpha"))
        assert res.agent_id == "data-scout@team-alpha"

    async def test_remote_agent_refused_without_bridge(
        self, tmp_path: Path
    ) -> None:
        """Spec criterion #25 / decision #14: ``remote_agent`` spawns are
        refused in v1 unless the bridge is explicitly enabled."""
        ex = RemoteExecutor(worktree_root=tmp_path, leader_id="leader-1")
        res = await ex.spawn(_cfg(task_type="remote_agent"))
        assert res.success is False
        assert res.error is not None
        assert "bridge" in res.error.lower()

    async def test_remote_spawn_raises_when_strict(self, tmp_path: Path) -> None:
        ex = RemoteExecutor(
            worktree_root=tmp_path, leader_id="leader-1", raise_on_disabled=True
        )
        with pytest.raises(BridgeDisabled):
            await ex.spawn(_cfg(task_type="remote_agent"))


# --- depth cap -----------------------------------------------------------


class TestDepthCap:
    async def test_subagent_depth_capped_at_three(self, tmp_path: Path) -> None:
        """Spec criterion #15: spawn at depth 4 is refused."""

        async def factory(cfg: TeammateSpawnConfig) -> str:
            return "ok"

        events: list[dict[str, Any]] = []
        ex = InProcessExecutor(
            worktree_root=tmp_path,
            leader_id="leader-1",
            factory=factory,
            event_sink=events.append,
        )

        # depth 3 (the max) is allowed
        ok = await ex.spawn(_cfg(depth=MAX_SUBAGENT_DEPTH))
        assert ok.success is True

        # depth 4 is refused
        res = await ex.spawn(_cfg(depth=MAX_SUBAGENT_DEPTH + 1))
        assert res.success is False
        assert "exceeded subagent depth" in (res.error or "").lower()

    async def test_subagent_depth_violation_emits_info_event(
        self, tmp_path: Path
    ) -> None:
        async def factory(cfg: TeammateSpawnConfig) -> str:
            return "ok"

        events: list[dict[str, Any]] = []
        ex = InProcessExecutor(
            worktree_root=tmp_path,
            leader_id="leader-1",
            factory=factory,
            event_sink=events.append,
        )
        await ex.spawn(_cfg(depth=MAX_SUBAGENT_DEPTH + 1))

        info = [e for e in events if e.get("type") == "subagent.depth_exceeded"]
        assert len(info) == 1
        assert info[0]["level"] == "info"
        assert info[0]["depth"] == MAX_SUBAGENT_DEPTH + 1


# --- spawn-config plumbing through the executor --------------------------


class TestPermissionPromptsDefault:
    async def test_role_spawned_with_allow_permission_prompts_false(
        self, tmp_path: Path
    ) -> None:
        """Spec criterion #16: spawned roles default to auto-deny."""
        seen: list[bool] = []

        async def factory(cfg: TeammateSpawnConfig) -> str:
            seen.append(cfg.allow_permission_prompts)
            return "ok"

        ex = InProcessExecutor(
            worktree_root=tmp_path, leader_id="leader-1", factory=factory
        )
        await ex.spawn(_cfg())
        await ex.wait_all()

        assert seen == [False]


# --- small async sleep helper that doesn't bring in asyncio import noise -


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


# --- BackendRegistry auto-detect -----------------------------------------


class TestBackendRegistry:
    def test_default_backend_is_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec decision #13: subprocess is the safe default; pane
        backends are deferred to a later slice."""
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
        reg = BackendRegistry(worktree_root=tmp_path, leader_id="leader-1")
        assert reg.detect_backend() == "subprocess"

    def test_in_process_explicit_selection(self, tmp_path: Path) -> None:
        reg = BackendRegistry(worktree_root=tmp_path, leader_id="leader-1")

        async def factory(cfg: TeammateSpawnConfig) -> str:
            return "ok"

        reg.set_in_process_factory(factory)
        ex = reg.get_executor("in_process")
        assert isinstance(ex, InProcessExecutor)

    def test_remote_returns_remote_executor(self, tmp_path: Path) -> None:
        reg = BackendRegistry(worktree_root=tmp_path, leader_id="leader-1")
        ex = reg.get_executor("remote")
        assert isinstance(ex, RemoteExecutor)

    def test_unknown_backend_raises(self, tmp_path: Path) -> None:
        reg = BackendRegistry(worktree_root=tmp_path, leader_id="leader-1")
        with pytest.raises(ValueError):
            reg.get_executor("tmux")  # type: ignore[arg-type]
