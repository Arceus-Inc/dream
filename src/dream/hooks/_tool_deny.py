"""PRE_TOOL_USE deny-list hook — Hermes ``pre_tool_call`` veto for named tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from dream.contracts.hook import HookEvent, HookResult, HookSpec

_DEFAULT_FEEDBACK_TEMPLATE = "tool {tool_name!r} is denied by policy"


@dataclass(frozen=True, slots=True)
class ToolDenyListConfig:
    """Tools that must never execute when this hook is registered."""

    denied: frozenset[str] = field(default_factory=frozenset)
    feedback_template: str = _DEFAULT_FEEDBACK_TEMPLATE


class ToolDenyListHook:
    """Opt-in ``allow_block`` veto for a fixed tool-name deny list."""

    def __init__(self, *, config: ToolDenyListConfig | None = None) -> None:
        self._config = config or ToolDenyListConfig()
        try:
            self._config.feedback_template.format(tool_name="tool")
        except (IndexError, KeyError, ValueError):
            raise ValueError("feedback_template must format with tool_name") from None
        self._feedback_template = self._config.feedback_template
        self.spec = HookSpec(
            events=(HookEvent.PRE_TOOL_USE,),
            priority=100,
            allow_block=True,
        )

    async def __call__(self, event: HookEvent, payload: Mapping[str, object]) -> HookResult:
        if event is not HookEvent.PRE_TOOL_USE:
            return HookResult()
        tool_name = str(payload.get("tool_name", ""))
        if tool_name not in self._config.denied:
            return HookResult()
        feedback = self._feedback_template.format(tool_name=tool_name)
        return HookResult(blocked=True, feedback=feedback)


__all__ = ["ToolDenyListConfig", "ToolDenyListHook"]
