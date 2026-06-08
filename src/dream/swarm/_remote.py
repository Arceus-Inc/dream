"""Remote-agent executor — bridge seam, gated off in v1.

Spec decision #14 + criterion #25: the ``BridgeConfig`` / ``WorkSecret``
types and the executor surface exist so the multi-host upgrade is a
configuration change, but in v1 every ``remote_agent`` spawn is refused
(either as an unsuccessful ``SpawnResult`` or — when ``raise_on_disabled``
is set — by raising :class:`BridgeDisabled`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dream.swarm._spawn import (
    BackendType,
    BridgeDisabled,
    SpawnResult,
    TeammateSpawnConfig,
)

__all__ = ["RemoteExecutor"]


@dataclass
class RemoteExecutor:
    """Always refuses ``remote_agent`` spawns until the bridge is enabled."""

    worktree_root: Path
    leader_id: str
    raise_on_disabled: bool = False
    type: BackendType = "remote"

    async def spawn(self, config: TeammateSpawnConfig) -> SpawnResult:
        message = (
            f"remote_agent spawn refused: bridge disabled in v1 "
            f"(task_type={config.task_type})"
        )
        if self.raise_on_disabled:
            raise BridgeDisabled(message)
        return SpawnResult(
            task_id="",
            agent_id=f"{config.name}@{config.team}",
            backend_type="remote",
            success=False,
            error=message,
        )

    async def shutdown(self, agent_id: str, *, force: bool = False) -> bool:
        return False
