"""Hook executor — bounded, crash-isolated lifecycle dispatch (spec 13).

Observers by default. Opt-in powers (Hermes-aligned):

- ``HookSpec.allow_block`` — ``HookResult.blocked`` becomes a real veto
  (``hook.blocked``); without the flag → ``hook.blocked.ignored``.
- ``HookSpec.allow_continue`` — ``continue_message`` on STOP is collected
  (first-wins); without the flag → ``hook.continue.ignored``.
- ``replacement_input`` / ``replacement_result`` / ``inject_context`` —
  first non-empty wins; call sites must apply them.

Handlers run under a wall-clock deadline (default 1s). Crash / timeout →
emit + move on (fail-open).
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
    blocked: bool = False
    replacement_input: dict[str, Any] | None = None
    replacement_result: str | None = None
    inject_context: str | None = None
    continue_message: str | None = None


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
        matched.sort(key=lambda h: -h.spec.priority)
        return matched

    async def fire(self, event: HookEvent, payload: dict[str, Any]) -> FireOutcome:
        """Run every subscriber for ``event``; never raises. Opt-in powers honored."""
        fired = errors = timeouts = 0
        feedback: list[str] = []
        blocked = False
        replacement_input: dict[str, Any] | None = None
        replacement_result: str | None = None
        inject_context: str | None = None
        continue_message: str | None = None

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
                if hook.spec.allow_block:
                    blocked = True
                    self._emit_safe(
                        "hook.blocked",
                        event=str(event),
                        hook=_hook_name(hook),
                        feedback=result.feedback,
                    )
                else:
                    self._emit_safe(
                        "hook.blocked.ignored",
                        event=str(event),
                        hook=_hook_name(hook),
                        feedback=result.feedback,
                    )

            if result.continue_message:
                if hook.spec.allow_continue:
                    if continue_message is None:
                        continue_message = result.continue_message
                else:
                    self._emit_safe(
                        "hook.continue.ignored",
                        event=str(event),
                        hook=_hook_name(hook),
                        feedback=result.continue_message,
                    )

            if result.replacement_input is not None and replacement_input is None:
                replacement_input = dict(result.replacement_input)
            if result.replacement_result and replacement_result is None:
                replacement_result = result.replacement_result
            if result.inject_context and inject_context is None:
                inject_context = result.inject_context
            if result.feedback:
                feedback.append(result.feedback)

        return FireOutcome(
            fired=fired,
            errors=errors,
            timeouts=timeouts,
            feedback=tuple(feedback),
            blocked=blocked,
            replacement_input=replacement_input,
            replacement_result=replacement_result,
            inject_context=inject_context,
            continue_message=continue_message,
        )

    def _emit_safe(self, event_type: str, **payload: Any) -> None:
        with contextlib.suppress(Exception):
            self._emit(event_type, **payload)


def _hook_name(hook: Hook) -> str:
    return type(hook).__qualname__
