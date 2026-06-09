"""Teammate spawn config, result, executor protocol, and depth cap.

Spec 10 §"Teammate spawn config" pins the runtime spawn handle and
§"Acceptance criteria" pin the depth cap (#15), the
``allow_permission_prompts: false`` default (#16), and the bridge refusal
(#25). All gathered here so the executor implementations stay small.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

__all__ = [
    "MAX_SUBAGENT_DEPTH",
    "BackendType",
    "BridgeDisabled",
    "SpawnResult",
    "SubagentTaskType",
    "TeammateExecutor",
    "TeammateSpawnConfig",
]

MAX_SUBAGENT_DEPTH: int = 3
"""Spec decision #9: depth 0=runner, 1=role, 2=reviewer, 3=remediation;
depth 4 is refused."""

BackendType = Literal["subprocess", "in_process", "remote"]
"""v1 backends. ``tmux`` / ``iterm2`` pane backends are deferred to a
later slice — they only affect visualisation, not the file bus."""

SubagentTaskType = Literal["local_agent", "remote_agent", "in_process_teammate"]
"""Spec decision #11: which #07 ``TaskType`` the spawn maps to."""


def _coerce_tokens(value: object, *, field_name: str) -> tuple[str, ...]:
    """Coerce a list/tuple of string tokens to a tuple, rejecting bare strings.

    A plain ``str`` is an iterable of characters, so accepting it would split a
    single token (e.g. ``"read"``) into ``("r", "e", "a", "d")``. Callers must
    pass an explicit sequence of tokens.
    """
    if isinstance(value, str):
        raise TypeError(
            f"{field_name} must be a sequence of strings, not a bare string "
            f"(got {value!r}); wrap a single value as a one-element list"
        )
    if isinstance(value, tuple):
        return value
    return tuple(value)  # type: ignore[arg-type]


class BridgeDisabled(RuntimeError):
    """Raised when a ``remote_agent`` spawn is attempted in v1 (the
    bridge seam is documented but gated off — spec decision #14)."""


@dataclass(frozen=True)
class TeammateSpawnConfig:
    """Runtime spawn handle — ephemeral; the durable record lives on disk
    as the ledger + contract."""

    name: str
    team: str
    prompt: str
    cwd: str
    parent_session_id: str
    depth: int = 1
    model: str | None = None
    system_prompt: str | None = None
    system_prompt_mode: Literal["default", "replace", "append"] | None = None
    permissions: tuple[str, ...] = ()
    plan_mode_required: bool = False
    allow_permission_prompts: bool = False
    worktree_path: str | None = None
    subscriptions: tuple[str, ...] = ()
    task_type: SubagentTaskType = "local_agent"

    def __post_init__(self) -> None:
        if self.depth < 1:
            raise ValueError(
                f"depth must be >= 1 (depth 0 is the top-level runner); got {self.depth}"
            )
        # ergonomics: accept a list/tuple of tokens and store a tuple. A bare
        # string is rejected, not iterated: ``"read"`` would otherwise coerce
        # to ``("r", "e", "a", "d")`` and silently corrupt the token set.
        object.__setattr__(
            self, "permissions", _coerce_tokens(self.permissions, field_name="permissions")
        )
        object.__setattr__(
            self,
            "subscriptions",
            _coerce_tokens(self.subscriptions, field_name="subscriptions"),
        )


@dataclass(frozen=True)
class SpawnResult:
    """Result from a teammate spawn attempt."""

    task_id: str
    agent_id: str
    backend_type: BackendType
    success: bool = True
    error: str | None = None


@runtime_checkable
class TeammateExecutor(Protocol):
    """Execution backend for teammates.

    Communication between teammates is repo-only (spec criterion #19), so
    the executor surface is intentionally small: ``spawn`` + ``shutdown``.
    Anything else (sending the worker a message, polling its inbox) goes
    through the file mailbox API from slice 10-B.
    """

    type: BackendType

    async def spawn(self, config: TeammateSpawnConfig) -> SpawnResult: ...
    async def shutdown(self, agent_id: str, *, force: bool = False) -> bool: ...
