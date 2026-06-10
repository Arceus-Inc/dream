"""Hook executor — fire-and-forget observers of the agent loop (spec 13).

Two hard rules from the spec's extension surface:

12. **Hooks never veto.** A ``HookResult.blocked=True`` is stripped and
    warned about (``hook.blocked.ignored``) — divergence #1 from
    OpenHarness, whose ``block_on_failure`` path is removed here.
13. **Synchronous-but-bounded:** each handler gets a wall-clock deadline
    (default 1s). Overrun → ``hook.handler.timeout``, move on, no retry.
    A crash → ``hook.handler.error``, move on. Real work belongs on a
    queue — that's the handler's problem, not the loop's.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from dream.contracts.hook import Hook, HookEvent

__all__ = ["FireOutcome", "HookExecutor"]

_DEFAULT_DEADLINE_SECONDS = 1.0


class _EmitFn(Protocol):
    def __call__(self, event_type: str, **payload: Any) -> Any: ...


def _no_emit(event_type: str, **payload: Any) -> None:
    return None


@dataclass(frozen=True)
class FireOutcome:
    """What one ``fire`` call observed across its handlers."""

    fired: int = 0
    errors: int = 0
    timeouts: int = 0
    feedback: tuple[str, ...] = field(default_factory=tuple)
    # Always False — kept so call sites read naturally; spec 13 strips
    # the veto path entirely.
    blocked: bool = False


class HookExecutor:
    """Dispatch lifecycle events to subscribed hooks, crash-isolated."""

    def __init__(
        self,
        hooks: Iterable[Hook] = (),
        *,
        emit: _EmitFn = _no_emit,
        deadline_seconds: float = _DEFAULT_DEADLINE_SECONDS,
    ) -> None:
        self._hooks: list[Hook] = list(hooks)
        self._emit = emit
        self._deadline = deadline_seconds

    def register(self, hook: Hook) -> None:
        self._hooks.append(hook)

    def _subscribers(self, event: HookEvent) -> list[Hook]:
        matched = [h for h in self._hooks if event in h.spec.events]
        # Higher priority first; ties keep registration order (sort is stable).
        matched.sort(key=lambda h: -h.spec.priority)
        return matched

    async def fire(self, event: HookEvent, payload: dict[str, Any]) -> FireOutcome:
        """Run every subscriber for ``event``; never raises, never vetoes."""
        fired = errors = timeouts = 0
        feedback: list[str] = []
        for hook in self._subscribers(event):
            fired += 1
            try:
                async with asyncio.timeout(self._deadline):
                    result = await hook(event, payload)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                timeouts += 1
                self._emit_safe(
                    "hook.handler.timeout",
                    event=str(event),
                    hook=_hook_name(hook),
                    deadline_seconds=self._deadline,
                )
                continue
            except Exception as exc:
                errors += 1
                self._emit_safe(
                    "hook.handler.error",
                    event=str(event),
                    hook=_hook_name(hook),
                    error=repr(exc),
                )
                continue
            if result.blocked:
                self._emit_safe(
                    "hook.blocked.ignored",
                    event=str(event),
                    hook=_hook_name(hook),
                    feedback=result.feedback,
                )
            if result.feedback:
                feedback.append(result.feedback)
        return FireOutcome(
            fired=fired,
            errors=errors,
            timeouts=timeouts,
            feedback=tuple(feedback),
        )

    def _emit_safe(self, event_type: str, **payload: Any) -> None:
        with contextlib.suppress(Exception):
            self._emit(event_type, **payload)


def _hook_name(hook: Hook) -> str:
    return type(hook).__qualname__
