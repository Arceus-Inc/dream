"""Verify-on-stop hook — Hermes ``pre_verify`` / verify-on-stop for Dream.

Tracks mutating tool success across a session. At STOP ``pre_seal``, if the
agent mutated the tree without running an evidence tool, request another turn
via ``continue_message`` (requires ``HookSpec.allow_continue``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dream.contracts.hook import HookEvent, HookResult, HookSpec
from dream.state.shadow import MutatingToolName


@dataclass(frozen=True, slots=True)
class _VerifyState:
    mutated: bool = False
    has_evidence: bool = False


@dataclass(frozen=True, slots=True)
class VerifyOnStopConfig:
    """Which tools count as mutations vs verification evidence."""

    require_evidence_for: frozenset[str] = frozenset(t.value for t in MutatingToolName)
    evidence_tools: frozenset[str] = frozenset(
        {
            "bash",
            "grep",
            "glob",
            "read_file",
        }
    )
    nudge_template: str = (
        "You edited files without running verification. "
        "Run tests (or another evidence check) before finishing."
    )


class VerifyOnStopHook:
    """Hermes-style STOP continue when mutations lack evidence."""

    def __init__(self, *, config: VerifyOnStopConfig | None = None) -> None:
        self._config = config or VerifyOnStopConfig()
        self.spec = HookSpec(
            events=(
                HookEvent.SESSION_START,
                HookEvent.POST_TOOL_USE,
                HookEvent.STOP,
            ),
            priority=50,
            allow_continue=True,
        )
        self._state: dict[str, _VerifyState] = {}

    def _reset(self, session_id: str) -> None:
        self._state[session_id] = _VerifyState()
        if len(self._state) > 64:
            oldest = next(iter(self._state))
            del self._state[oldest]

    async def __call__(self, event: HookEvent, payload: Mapping[str, object]) -> HookResult:
        if event is HookEvent.SESSION_START:
            self._reset(str(payload.get("session_id", "")))
            return HookResult()

        if event is HookEvent.POST_TOOL_USE:
            session_id = str(payload.get("session_id", ""))
            state = self._state.get(session_id, _VerifyState())
            tool_name = str(payload.get("tool_name", ""))
            is_error = bool(payload.get("is_error", False))
            if is_error:
                return HookResult()
            if tool_name in self._config.require_evidence_for:
                state = _VerifyState(mutated=True)
            if tool_name in self._config.evidence_tools:
                state = _VerifyState(mutated=state.mutated, has_evidence=True)
            self._state[session_id] = state
            if len(self._state) > 64:
                oldest = next(iter(self._state))
                del self._state[oldest]
            return HookResult()

        if event is HookEvent.STOP:
            session_id = str(payload.get("session_id", ""))
            state = self._state.get(session_id, _VerifyState())
            phase = str(payload.get("phase", ""))
            if phase != "pre_seal":
                return HookResult()
            if state.mutated and not state.has_evidence:
                return HookResult(continue_message=self._config.nudge_template)
            return HookResult()

        return HookResult()


__all__ = ["VerifyOnStopConfig", "VerifyOnStopHook"]
