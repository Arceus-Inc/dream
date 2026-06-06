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
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from dream.engine._events import AssistantTurnComplete
from dream.engine._loop import TurnStreamer
from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)
from dream.wake._decision import HeartbeatDecision
from dream.wake._prompt import load_heartbeat_prompt
from dream.wake._tool import HeartbeatInput


def _default_now() -> datetime:
    return datetime.now(UTC)


def _build_stimulus(system_prompt: str, wake_source: str) -> ConversationMessage:
    """The single user message that drives the wake turn.

    Concatenating the heartbeat prompt with the wake source into one user
    message keeps the runner free of any system-prompt plumbing in the
    provider adapter — slice 2 can refactor this into a real system role
    once the streamer surface grows one.
    """
    text = (
        f"{system_prompt.rstrip()}\n\n"
        f"Wake source: {wake_source}\n"
        f"Decide now by calling the heartbeat tool."
    )
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _decision_from_block(
    block: ToolUseBlock,
    *,
    wake_source: str,
    decided_at: datetime,
) -> HeartbeatDecision | None:
    """Build a decision from a ``heartbeat`` tool-use block, or ``None`` if invalid.

    Returns ``None`` for schema-invalid args so the caller can record a
    ``missing`` outcome with a uniform reason string.
    """
    try:
        parsed = HeartbeatInput.model_validate(block.input)
    except (ValidationError, ValueError):
        return None
    tasks = () if parsed.action == "skip" else tuple(parsed.tasks)
    return HeartbeatDecision(
        decided_at=decided_at,
        action=parsed.action,
        tasks=tasks,
        reason=parsed.reason,
        wake_source=wake_source,
        forced=False,
        outcome="decided",
    )


def _missing(decided_at: datetime, wake_source: str) -> HeartbeatDecision:
    return HeartbeatDecision(
        decided_at=decided_at,
        action="skip",
        tasks=(),
        reason="heartbeat_missing_decision",
        wake_source=wake_source,
        forced=False,
        outcome="missing",
    )


async def run_background_turn(
    streamer: TurnStreamer,
    *,
    wake_source: str,
    system_prompt: str | None = None,
    prompt_override_path: Path | None = None,
    now: Callable[[], datetime] = _default_now,
) -> HeartbeatDecision:
    """Drive exactly one model turn and return the captured decision.

    Resolution order for the wake system prompt:

    1. ``system_prompt`` (literal string), if given;
    2. else ``prompt_override_path`` if it exists;
    3. else the bundled default (``BUNDLED_HEARTBEAT_PROMPT``).

    The runner consumes events until the first ``AssistantTurnComplete``
    and never re-enters the model.
    """
    prompt = (
        system_prompt
        if system_prompt is not None
        else load_heartbeat_prompt(prompt_override_path)
    )
    stimulus = _build_stimulus(prompt, wake_source)
    decided_at = now()

    captured: ToolUseBlock | None = None
    async for ev in streamer.stream_turn([stimulus]):
        if isinstance(ev, AssistantTurnComplete):
            for block in ev.blocks:
                if isinstance(block, ToolUseBlock) and block.name == "heartbeat":
                    captured = block
                    break
            break

    if captured is None:
        return _missing(decided_at, wake_source)
    decision = _decision_from_block(
        captured, wake_source=wake_source, decided_at=decided_at
    )
    return decision if decision is not None else _missing(decided_at, wake_source)


__all__ = ["run_background_turn"]
