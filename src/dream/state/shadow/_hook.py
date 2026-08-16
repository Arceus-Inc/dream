"""PRE_TOOL_USE hook that snaps the worktree before mutating tools."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dream.contracts.hook import HookEvent, HookResult, HookSpec
from dream.state.shadow._manager import ShadowCheckpointManager
from dream.state.shadow._types import reason_for_tool


class ShadowCheckpointHook:
    """Hermes-style pre-mutate checkpoint as a Dream lifecycle hook.

    Observes ``PRE_TOOL_USE`` (mutating tools only) and resets per-turn
    dedup on ``USER_PROMPT_SUBMIT``. Never blocks.
    """

    spec = HookSpec(
        events=(HookEvent.PRE_TOOL_USE, HookEvent.USER_PROMPT_SUBMIT),
        priority=40,
    )

    def __init__(
        self,
        *,
        manager: ShadowCheckpointManager,
        working_dir: Path,
    ) -> None:
        self._manager = manager
        self._working_dir = working_dir

    @staticmethod
    def _session_id(payload: Mapping[str, Any]) -> str | None:
        raw_session_id = payload.get("session_id")
        return str(raw_session_id) if raw_session_id is not None else None

    async def __call__(self, event: HookEvent, payload: Mapping[str, Any]) -> HookResult:
        if event is HookEvent.USER_PROMPT_SUBMIT:
            self._manager.begin_turn(self._session_id(payload))
            return HookResult()

        if event is not HookEvent.PRE_TOOL_USE:
            return HookResult()

        tool_name = str(payload.get("tool_name", ""))
        reason = reason_for_tool(tool_name)
        if reason is None:
            return HookResult()

        self._manager.ensure(
            self._working_dir,
            reason=reason,
            session_id=self._session_id(payload),
        )
        return HookResult()


__all__ = ["ShadowCheckpointHook"]
