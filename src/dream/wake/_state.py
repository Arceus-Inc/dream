"""Spec 06.5 slice 2 — per-agent persistent heartbeat state.

The skip-streak counter is the load-bearing piece of the anti-coma
guard: it lives on disk so a process restart can't reset a depressed
agent's streak to zero. Storage is a single JSON file per agent under
the coordination dir.

Reads are forgiving (default on missing/torn/unknown-field). Writes use
``json.dump`` with a temp-file + rename for crash safety. Concurrency is
the orchestrator's responsibility — it holds the per-agent lock around
the read-modify-write.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from dream.utils.fs import atomic_write_text


@dataclass(frozen=True)
class HeartbeatState:
    """Per-agent persistent counter + last-decision timestamp."""

    skip_streak: int = 0
    last_decided_at: datetime | None = None

    def with_skip_streak(self, n: int) -> HeartbeatState:
        return replace(self, skip_streak=n)

    def with_last_decided_at(self, t: datetime) -> HeartbeatState:
        return replace(self, last_decided_at=t)


def state_path_for(coordination_dir: Path, *, agent_id: str) -> Path:
    """Compute the heartbeat-state path for an agent under a coordination dir.

    ``agent_id`` is a filename fragment so it must not contain path
    separators and must not be empty.
    """
    if not agent_id:
        raise ValueError("agent_id must not be empty")
    if "/" in agent_id or "\\" in agent_id:
        raise ValueError(f"agent_id must not contain path separators: {agent_id!r}")
    return coordination_dir / f"heartbeat-{agent_id}.skip-streak.json"


def read_state(path: Path) -> HeartbeatState:
    """Read state from ``path``. Returns default on any read/parse failure."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return HeartbeatState()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return HeartbeatState()
    if not isinstance(data, dict):
        return HeartbeatState()
    skip = data.get("skip_streak", 0)
    if not isinstance(skip, int) or skip < 0:
        skip = 0
    last_raw = data.get("last_decided_at")
    last: datetime | None
    if isinstance(last_raw, str):
        try:
            last = datetime.fromisoformat(last_raw)
        except ValueError:
            last = None
    else:
        last = None
    return HeartbeatState(skip_streak=skip, last_decided_at=last)


def write_state(path: Path, state: HeartbeatState) -> None:
    """Write state to ``path``, creating parent dirs as needed.

    Uses a temp-file + atomic rename so a crash mid-write can't leave a
    half-written file (which ``read_state`` would tolerate anyway, but
    we prefer not to rely on that).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"skip_streak": state.skip_streak}
    if state.last_decided_at is not None:
        payload["last_decided_at"] = state.last_decided_at.isoformat()
    atomic_write_text(path, json.dumps(payload))
