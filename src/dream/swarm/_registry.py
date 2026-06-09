"""Team registry + backend registry.

Two registries with two very different lifetimes:

- :class:`TeamRegistry` — *persistent* team metadata under
  ``<worktree>/.harness/swarm/teams/{team}/team.json`` (atomic writes via
  :func:`dream.utils.fs.atomic_write_text`). The shape mirrors the
  OpenHarness ``TeamFile`` so an existing home-rooted teams directory
  could be re-rooted here, but the location lives in the worktree per
  spec-10 §"Repo-only communication".

- :class:`BackendRegistry` — *runtime* dispatcher mapping a
  :data:`~dream.swarm._spawn.BackendType` literal to the concrete
  :class:`~dream.swarm._spawn.TeammateExecutor` instance for this leader.
  Auto-detection is deliberately conservative in v1: ``subprocess`` is the
  default; ``in_process`` is selected explicitly when a factory is wired;
  ``remote`` returns the gated :class:`~dream.swarm._remote.RemoteExecutor`.
  Pane backends (``tmux`` / ``iterm2``) are deferred to a later slice.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dream.swarm._identity import sanitize_team_name
from dream.swarm._remote import RemoteExecutor
from dream.swarm._spawn import (
    BackendType,
    TeammateExecutor,
)
from dream.swarm.in_process import InProcessExecutor, InProcessFactory
from dream.swarm.subprocess_backend import ArgvBuilder, SubprocessExecutor
from dream.tasks._manager import BackgroundTaskManager
from dream.utils.fs import atomic_write_text

__all__ = [
    "BackendRegistry",
    "TeamFile",
    "TeamMember",
    "TeamRegistry",
]


# --- TeamFile / TeamMember ----------------------------------------------


@dataclass
class TeamMember:
    """Persistent record for one teammate inside a :class:`TeamFile`."""

    agent_id: str
    name: str
    team: str
    backend_type: BackendType
    joined_at: float
    agent_type: str | None = None
    model: str | None = None
    prompt: str | None = None
    color: str | None = None
    plan_mode_required: bool = False
    session_id: str | None = None
    subscriptions: list[str] = field(default_factory=list)
    cwd: str = ""
    worktree_path: str | None = None
    permissions: list[str] = field(default_factory=list)
    status: Literal["active", "idle", "stopped"] = "active"

    def to_dict(self) -> dict[str, Any]:
        # Round-trips the 16 fields below; backend_type is a BackendType
        # literal, status is "active"|"idle"|"stopped", subscriptions and
        # permissions are list[str], the rest are scalars or null.
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "team": self.team,
            "backend_type": self.backend_type,
            "joined_at": self.joined_at,
            "agent_type": self.agent_type,
            "model": self.model,
            "prompt": self.prompt,
            "color": self.color,
            "plan_mode_required": self.plan_mode_required,
            "session_id": self.session_id,
            "subscriptions": list(self.subscriptions),
            "cwd": self.cwd,
            "worktree_path": self.worktree_path,
            "permissions": list(self.permissions),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamMember:
        return cls(
            agent_id=data["agent_id"],
            name=data["name"],
            team=data["team"],
            backend_type=data["backend_type"],
            joined_at=float(data["joined_at"]),
            agent_type=data.get("agent_type"),
            model=data.get("model"),
            prompt=data.get("prompt"),
            color=data.get("color"),
            plan_mode_required=bool(data.get("plan_mode_required", False)),
            session_id=data.get("session_id"),
            subscriptions=list(data.get("subscriptions") or []),
            cwd=data.get("cwd", ""),
            worktree_path=data.get("worktree_path"),
            permissions=list(data.get("permissions") or []),
            status=data.get("status", "active"),
        )


@dataclass
class TeamFile:
    """The on-disk representation of a swarm team."""

    name: str
    created_at: float
    description: str = ""
    lead_agent_id: str = ""
    lead_session_id: str | None = None
    members: dict[str, TeamMember] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        # On-disk team.json shape:
        #   {"name": str, "description": str, "created_at": float,
        #    "lead_agent_id": str, "lead_session_id": str | None,
        #    "members": {agent_id: <TeamMember.to_dict()>},
        #    "metadata": dict[str, Any]}
        return {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "lead_agent_id": self.lead_agent_id,
            "lead_session_id": self.lead_session_id,
            "members": {k: v.to_dict() for k, v in self.members.items()},
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamFile:
        members = {
            k: TeamMember.from_dict(v) for k, v in (data.get("members") or {}).items()
        }
        return cls(
            name=data["name"],
            created_at=float(data["created_at"]),
            description=data.get("description", ""),
            lead_agent_id=data.get("lead_agent_id", ""),
            lead_session_id=data.get("lead_session_id"),
            members=members,
            metadata=dict(data.get("metadata") or {}),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> TeamFile:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)


# --- TeamRegistry --------------------------------------------------------


_TEAM_FILE = "team.json"


class TeamRegistry:
    """File-backed CRUD over ``<worktree>/.harness/swarm/teams/``."""

    def __init__(self, worktree_root: Path) -> None:
        self._root = Path(worktree_root)

    @property
    def teams_root(self) -> Path:
        return self._root / ".harness" / "swarm" / "teams"

    def _team_path(self, name: str) -> Path:
        return self.teams_root / name / _TEAM_FILE

    def create_team(self, *, name: str, description: str = "") -> TeamFile:
        sname = sanitize_team_name(name)
        path = self._team_path(sname)
        if path.exists():
            raise FileExistsError(f"team {sname!r} already exists at {path}")
        tf = TeamFile(name=sname, description=description, created_at=time.time())
        tf.save(path)
        return tf

    def get_team(self, name: str) -> TeamFile:
        sname = sanitize_team_name(name)
        path = self._team_path(sname)
        if not path.exists():
            raise FileNotFoundError(f"team {sname!r} not found at {path}")
        return TeamFile.load(path)

    def list_teams(self) -> list[str]:
        if not self.teams_root.exists():
            return []
        return sorted(
            p.name
            for p in self.teams_root.iterdir()
            if p.is_dir() and (p / _TEAM_FILE).exists()
        )

    def add_member(self, team: str, member: TeamMember) -> TeamFile:
        tf = self.get_team(team)
        tf.members[member.agent_id] = member
        tf.save(self._team_path(tf.name))
        return tf

    def remove_member(self, team: str, agent_id: str) -> TeamFile:
        tf = self.get_team(team)
        tf.members.pop(agent_id, None)
        tf.save(self._team_path(tf.name))
        return tf


# --- BackendRegistry -----------------------------------------------------


class BackendRegistry:
    """Maps a :data:`BackendType` to its :class:`TeammateExecutor` instance.

    The default backend is ``subprocess`` (decision #13); ``in_process`` is
    only available once a factory is registered (the leader supplies it);
    ``remote`` always returns a refusing :class:`RemoteExecutor` (decision
    #14). Pane backends (``tmux``/``iterm2``) are deferred and raise
    ``ValueError`` here.
    """

    def __init__(
        self,
        *,
        worktree_root: Path,
        leader_id: str,
        task_manager: BackgroundTaskManager | None = None,
        argv_builder: ArgvBuilder | None = None,
    ) -> None:
        self._worktree_root = Path(worktree_root)
        self._leader_id = leader_id
        self._task_manager = task_manager
        self._argv_builder = argv_builder
        self._in_process_factory: InProcessFactory | None = None
        self._instances: dict[BackendType, TeammateExecutor] = {}

    def set_in_process_factory(self, factory: InProcessFactory) -> None:
        self._in_process_factory = factory
        # rebuild on next get
        self._instances.pop("in_process", None)

    def set_subprocess_argv_builder(self, builder: ArgvBuilder) -> None:
        self._argv_builder = builder
        self._instances.pop("subprocess", None)

    def set_task_manager(self, manager: BackgroundTaskManager) -> None:
        self._task_manager = manager
        self._instances.pop("subprocess", None)

    def detect_backend(self) -> BackendType:
        """Auto-detect the best available backend.

        v1: pane backends are deferred even when ``$TMUX`` /
        ``$ITERM_SESSION_ID`` are set, so the answer is always
        ``subprocess`` — the env vars are checked only to make the
        intent explicit and to keep the seam ready for slice 10-H.
        """
        # Read the env vars so the seam exists; they don't change the
        # answer in v1.
        _ = os.environ.get("TMUX")
        _ = os.environ.get("ITERM_SESSION_ID")
        return "subprocess"

    def get_executor(self, backend: BackendType) -> TeammateExecutor:
        if backend in self._instances:
            return self._instances[backend]
        builders: dict[BackendType, Callable[[], TeammateExecutor]] = {
            "in_process": self._build_in_process,
            "subprocess": self._build_subprocess,
            "remote": self._build_remote,
        }
        builder = builders.get(backend)
        if builder is None:
            raise ValueError(
                f"backend {backend!r} not available in v1 "
                "(pane backends are deferred to a later slice)"
            )
        ex = builder()
        self._instances[backend] = ex
        return ex

    def _build_in_process(self) -> TeammateExecutor:
        if self._in_process_factory is None:
            raise ValueError(
                "in_process executor requested but no factory was registered "
                "(use BackendRegistry.set_in_process_factory)"
            )
        return InProcessExecutor(
            worktree_root=self._worktree_root,
            leader_id=self._leader_id,
            factory=self._in_process_factory,
        )

    def _build_subprocess(self) -> TeammateExecutor:
        if self._task_manager is None:
            raise ValueError(
                "subprocess executor requested but no BackgroundTaskManager "
                "was provided to BackendRegistry"
            )
        kw: dict[str, Any] = {
            "worktree_root": self._worktree_root,
            "leader_id": self._leader_id,
            "task_manager": self._task_manager,
        }
        if self._argv_builder is not None:
            kw["argv_builder"] = self._argv_builder
        return SubprocessExecutor(**kw)

    def _build_remote(self) -> TeammateExecutor:
        return RemoteExecutor(
            worktree_root=self._worktree_root,
            leader_id=self._leader_id,
        )
