"""Task-level subagents (Harness.spawn_agent). Not org-level."""

from dream.swarm._identity import (
    TeammateIdentity,
    sanitize_agent_name,
    sanitize_team_name,
)
from dream.swarm._registry import (
    BackendRegistry,
    TeamFile,
    TeamMember,
    TeamRegistry,
)
from dream.swarm._remote import RemoteExecutor
from dream.swarm._spawn import (
    MAX_SUBAGENT_DEPTH,
    BackendType,
    BridgeDisabled,
    SpawnResult,
    SubagentTaskType,
    TeammateExecutor,
    TeammateSpawnConfig,
)
from dream.swarm.in_process import InProcessExecutor, InProcessFactory
from dream.swarm.subprocess_backend import ArgvBuilder, SubprocessExecutor

__all__ = [
    "MAX_SUBAGENT_DEPTH",
    "ArgvBuilder",
    "BackendRegistry",
    "BackendType",
    "BridgeDisabled",
    "InProcessExecutor",
    "InProcessFactory",
    "RemoteExecutor",
    "SpawnResult",
    "SubagentTaskType",
    "SubprocessExecutor",
    "TeamFile",
    "TeamMember",
    "TeamRegistry",
    "TeammateExecutor",
    "TeammateIdentity",
    "TeammateSpawnConfig",
    "sanitize_agent_name",
    "sanitize_team_name",
]
