"""``run_background_turn`` — single-turn wake orchestrator.

This is intentionally a tiny dedicated runner, NOT a thin wrapper around
``dream.engine._session.run_session``. The wake turn needs none of
orientation, reviewer, compaction, or the liveness/coma monitor; folding
those in would couple the wake decision to the full session FSM for no
benefit. One model turn in, one ``HeartbeatDecision`` out.

The ``heartbeat`` tool is "virtual": we read the first ``ToolUseBlock``
with ``name == "heartbeat"`` directly off the assistant turn and convert
its input dict into a decision. The provider still sees the tool's schema
(so the model knows what shape to send) but no ``ToolDispatcher`` is
invoked — the wake runner has no working dir, no scratch dir, no need to
write a ``ToolResultBlock`` because the session ends after one turn.

Slice 2 added the ``forced=True`` mode used by the anti-coma guard. In
forced mode the wake stimulus picks up an addendum that names the
declined-skip count, and the runner refuses to honour a ``skip`` /
``missing`` outcome — both are synthesised up to a ``run`` decision with
``forced=True`` recorded for the audit trail.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from dream.engine._events import AssistantTurnComplete, StreamEvent
from dream.engine._loop import TurnStreamer
from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)
from dream.wake._decision import HeartbeatDecision
from dream.wake._prompt import forced_addendum, load_heartbeat_prompt
from dream.wake._source import WakeSource
from dream.wake._tool import HeartbeatInput


def _default_now() -> datetime:
    return datetime.now(UTC)


def _build_stimulus(
    system_prompt: str,
    wake_source: WakeSource,
    *,
    forced: bool,
    forced_skip_streak: int,
    extra_context: str | None = None,
) -> ConversationMessage:
    """The single user message that drives the wake turn.

    The prompt body is used verbatim — operator override prompts can carry
    deliberate trailing formatting, so we do NOT trim it. ``extra_context``
    (e.g. cron notes queued since the last wake — spec 15 hardening 2) is
    appended after the body so the checklist stays primary.
    """
    body = system_prompt
    if forced:
        body = body + forced_addendum(forced_skip_streak)
    if extra_context:
        body = f"{body}\n\n{extra_context}"
    text = (
        f"{body}\n\n"
        f"Wake source: {wake_source.label}\n"
        f"Decide now by calling the heartbeat tool."
    )
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _decision_from_block(
    block: ToolUseBlock,
    *,
    wake_source: WakeSource,
    decided_at: datetime,
    forced: bool,
) -> HeartbeatDecision | None:
    """Build a decision from a ``heartbeat`` tool-use block, or ``None`` if invalid.

    When ``forced`` is set, a model-emitted ``skip`` is overridden to a
    synthesised forced ``run`` (the narrowed schema *should* have prevented
    it, but defence in depth).
    """
    try:
        parsed = HeartbeatInput.model_validate(block.input)
    except (ValidationError, ValueError):
        return None
    if forced and parsed.action == "skip":
        return _forced_run(
            decided_at=decided_at,
            wake_source=wake_source,
            reason="forced run after declined skip",
        )
    tasks = () if parsed.action == "skip" else tuple(parsed.tasks)
    return HeartbeatDecision(
        decided_at=decided_at,
        action=parsed.action,
        tasks=tasks,
        reason=parsed.reason,
        wake_source=wake_source,
        forced=forced and parsed.action == "run",
        outcome="decided",
    )


def _missing(decided_at: datetime, wake_source: WakeSource) -> HeartbeatDecision:
    return HeartbeatDecision(
        decided_at=decided_at,
        action="skip",
        tasks=(),
        reason="heartbeat_missing_decision",
        wake_source=wake_source,
        forced=False,
        outcome="missing",
    )


def _forced_run(
    *,
    decided_at: datetime,
    wake_source: WakeSource,
    reason: str,
) -> HeartbeatDecision:
    return HeartbeatDecision(
        decided_at=decided_at,
        action="run",
        tasks=(),
        reason=reason,
        wake_source=wake_source,
        forced=True,
        outcome="decided",
    )


async def run_background_turn(
    streamer: TurnStreamer,
    *,
    wake_source: WakeSource,
    system_prompt: str | None = None,
    prompt_override_path: Path | None = None,
    forced: bool = False,
    forced_skip_streak: int = 0,
    extra_context: str | None = None,
    now: Callable[[], datetime] = _default_now,
) -> HeartbeatDecision:
    """Drive exactly one model turn and return the captured decision.

    Resolution order for the wake system prompt:

    1. ``system_prompt`` (literal string), if given;
    2. else ``prompt_override_path`` if it exists;
    3. else the bundled default (``BUNDLED_HEARTBEAT_PROMPT``).

    When ``forced=True`` the anti-coma addendum is appended to the
    stimulus, and the runner synthesises a forced ``run`` decision for
    silent / skip outcomes.
    """
    prompt = (
        system_prompt
        if system_prompt is not None
        else load_heartbeat_prompt(prompt_override_path)
    )
    stimulus = _build_stimulus(
        prompt,
        wake_source,
        forced=forced,
        forced_skip_streak=forced_skip_streak,
        extra_context=extra_context,
    )

    captured: ToolUseBlock | None = None
    # ``aclosing`` guarantees the turn stream — which may own transport
    # resources — is closed even though we break out of it on the first
    # ``AssistantTurnComplete`` (or if the caller cancels us mid-iteration).
    turn_stream = streamer.stream_turn([stimulus])
    async with contextlib.aclosing(
        cast(AsyncGenerator[StreamEvent, None], turn_stream)
    ):
        async for ev in turn_stream:
            if isinstance(ev, AssistantTurnComplete):
                for block in ev.blocks:
                    if isinstance(block, ToolUseBlock) and block.name == "heartbeat":
                        captured = block
                        break
                break

    # Captured after the turn produces (or fails to produce) a decision, so
    # ``decided_at`` records the decision time, not the request-start time.
    decided_at = now()

    if captured is None:
        if forced:
            return _forced_run(
                decided_at=decided_at,
                wake_source=wake_source,
                reason="forced run synthesised after silent wake",
            )
        return _missing(decided_at, wake_source)
    decision = _decision_from_block(
        captured, wake_source=wake_source, decided_at=decided_at, forced=forced
    )
    if decision is not None:
        return decision
    if forced:
        return _forced_run(
            decided_at=decided_at,
            wake_source=wake_source,
            reason="forced run synthesised after invalid heartbeat",
        )
    return _missing(decided_at, wake_source)


__all__ = ["run_background_turn"]
