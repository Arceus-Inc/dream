"""Spec 04 stage 4c — turn-aware compaction orchestrator + reactive PTL.

Wraps the deterministic 4b primitives (microcompact, atom-safe boundary,
attachment rebuild, PTL collapse/truncate) in the policy the engine wants:

- *Auto* trigger fires once per turn at the configured threshold; if the
  cheap tier (microcompact) doesn't free enough room and a ``summariser``
  is wired, escalate to the *full* tier; otherwise let the engine end the
  turn with outcome ``context-pressure`` (Spec 04 acceptance #4).
- *Reactive* (prompt-too-long) trigger collapses oversized text first and
  falls back to head-truncation; one retry per provider call.
- Compactor failures bump a counter the reset module reads (Spec 04 #9).

The summariser is *injected* — Spec 04 explicitly defers the compactor
prompt template, so this module only owns the orchestration shape, not
the prompt itself.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dream.contracts.provider import ProviderCapabilities
from dream.engine._messages import ConversationMessage
from dream.services.compact import (
    DEFAULT_KEEP_RECENT,
    CompactionResult,
    build_compact_attachments,
    build_post_compact_messages,
    create_compact_boundary_message,
    microcompact_messages,
    record_compact_checkpoint,
    split_preserving_tool_pairs,
    truncate_head_for_ptl_retry,
    try_context_collapse,
)
from dream.services.context_log import (
    CompactTrigger,
    ContextCompactionCompleted,
    ContextCompactionTriggered,
    ContextEvent,
)
from dream.services.compact._summariser import inject_todo_snapshot
from dream.services.token_estimation import (
    estimate_conversation_tokens,
    should_auto_compact,
    utilisation,
)

EventSink = Callable[[ContextEvent], None]
SummariserFn = Callable[
    [list[ConversationMessage]],
    list[ConversationMessage] | Awaitable[list[ConversationMessage]],
]

# Triggers that bypass the same-turn cooldown. ``reactive`` rescues an
# in-flight provider call so it MUST be allowed even after a same-turn
# auto compaction (Spec 04 §"Key decisions" #4 — last sentence).
_COOLDOWN_EXEMPT: frozenset[CompactTrigger] = frozenset({"reactive", "manual"})


# --- state -------------------------------------------------------------------


@dataclass
class AutoCompactState:
    """Mutable cross-turn state for the orchestrator.

    ``compacted_this_turn`` is the cooldown flag; the engine resets it via
    :func:`begin_turn` at the start of each turn. ``consecutive_failures``
    is the compactor-failure counter Spec 04 #9 reads for the reset trigger.
    """

    compacted_this_turn: bool = False
    consecutive_failures: int = 0
    turn_id: str = ""
    # Authoritative post-compact transcript from the last successful run.
    # Session mirror copies this instead of re-running a (possibly LLM) summariser.
    last_compacted_transcript: list[ConversationMessage] | None = field(default=None, repr=False)


def begin_turn(state: AutoCompactState, *, turn_id: str) -> None:
    """Reset per-turn flags at the start of each engine turn."""
    state.compacted_this_turn = False
    state.turn_id = turn_id


async def _invoke_summariser(
    summariser: SummariserFn,
    older: list[ConversationMessage],
) -> list[ConversationMessage]:
    result = summariser(older)
    if inspect.isawaitable(result):
        return await result
    return result


def _store_compact_result(
    state: AutoCompactState,
    rebuilt: list[ConversationMessage],
) -> None:
    state.last_compacted_transcript = list(rebuilt)


# --- orchestrator ------------------------------------------------------------


def auto_compact_if_needed(
    messages: list[ConversationMessage],
    *,
    capabilities: ProviderCapabilities | None,
    state: AutoCompactState,
    trigger: CompactTrigger = "auto",
    threshold: float = 0.7,
    preserve_recent: int = DEFAULT_KEEP_RECENT,
    summariser: SummariserFn | None = None,
    # Open continuity-state dict threaded to the attachment builders +
    # checkpoint trail; see ``compact._attachments`` module docstring for the
    # recognized keys (exec_plan_filename, blocked_steps, failing_tests, …).
    carryover_metadata: dict[str, Any] | None = None,
    event_sink: EventSink | None = None,
    force: bool = False,
    working_dir: Path | None = None,
) -> tuple[list[ConversationMessage], CompactionResult | None]:
    """Run compaction if needed; return ``(messages, result_or_none)``.

    ``result is None`` means nothing ran (cooldown, under-threshold, or the
    summariser failed). The engine reads :func:`utilisation` again on
    return to detect lingering context pressure.
    """
    if (
        trigger == "auto"
        and state.compacted_this_turn
        and not force
    ):
        return messages, None

    pre_util = utilisation(messages, capabilities)
    needs_compaction = (
        force
        or trigger in _COOLDOWN_EXEMPT
        or should_auto_compact(messages, capabilities, threshold=threshold)
    )
    if not needs_compaction:
        return messages, None

    _emit(event_sink, ContextCompactionTriggered(utilisation=pre_util, trigger=trigger))
    record_compact_checkpoint(
        carryover_metadata,
        checkpoint=f"{trigger}_triggered",
        trigger=trigger,
        message_count=len(messages),
        token_count=estimate_conversation_tokens(messages),
    )

    # --- Tier 1: microcompact -----------------------------------------------
    microcompacted, tokens_freed = microcompact_messages(
        messages, keep_recent=preserve_recent
    )
    record_compact_checkpoint(
        carryover_metadata,
        checkpoint="microcompact_end",
        trigger=trigger,
        message_count=len(microcompacted),
        token_count=estimate_conversation_tokens(microcompacted),
        details={"tokens_freed": tokens_freed},
    )

    post_util = utilisation(microcompacted, capabilities)
    microcompact_sufficient = post_util < threshold
    if microcompact_sufficient or summariser is None:
        # Microcompact-only result: rebuild the contract via attachments so
        # downstream consumers always see the same shape.
        result = _build_microcompact_result(
            microcompacted,
            trigger=trigger,
            preserve_recent=preserve_recent,
            carryover_metadata=carryover_metadata,
            pre_compact_message_count=len(messages),
            pre_compact_token_count=estimate_conversation_tokens(messages),
        )
        state.compacted_this_turn = True
        # Return the rebuilt post-compact transcript (boundary + attachments
        # included) so the micro tier honours the same contract as full.
        rebuilt = build_post_compact_messages(result)
        _emit(
            event_sink,
            ContextCompactionCompleted(
                tier="microcompact",
                preserved_attachments=len(result.attachments),
                resulting_utilisation=utilisation(rebuilt, capabilities),
            ),
        )
        _store_compact_result(state, rebuilt)
        return rebuilt, result

    # --- Tier 2: full LLM summarisation -------------------------------------
    return _run_full_tier_sync(
        messages,
        microcompacted=microcompacted,
        capabilities=capabilities,
        state=state,
        trigger=trigger,
        preserve_recent=preserve_recent,
        summariser=summariser,
        carryover_metadata=carryover_metadata,
        event_sink=event_sink,
        pre_compact_message_count=len(messages),
        pre_compact_token_count=estimate_conversation_tokens(messages),
        working_dir=working_dir,
    )


async def auto_compact_if_needed_async(
    messages: list[ConversationMessage],
    *,
    capabilities: ProviderCapabilities | None,
    state: AutoCompactState,
    trigger: CompactTrigger = "auto",
    threshold: float = 0.7,
    preserve_recent: int = DEFAULT_KEEP_RECENT,
    summariser: SummariserFn | None = None,
    carryover_metadata: dict[str, Any] | None = None,
    event_sink: EventSink | None = None,
    force: bool = False,
    working_dir: Path | None = None,
) -> tuple[list[ConversationMessage], CompactionResult | None]:
    """Async entry point — awaits async summarisers; sync summarisers still work."""
    if (
        trigger == "auto"
        and state.compacted_this_turn
        and not force
    ):
        return messages, None

    pre_util = utilisation(messages, capabilities)
    needs_compaction = (
        force
        or trigger in _COOLDOWN_EXEMPT
        or should_auto_compact(messages, capabilities, threshold=threshold)
    )
    if not needs_compaction:
        return messages, None

    _emit(event_sink, ContextCompactionTriggered(utilisation=pre_util, trigger=trigger))
    record_compact_checkpoint(
        carryover_metadata,
        checkpoint=f"{trigger}_triggered",
        trigger=trigger,
        message_count=len(messages),
        token_count=estimate_conversation_tokens(messages),
    )

    microcompacted, tokens_freed = microcompact_messages(
        messages, keep_recent=preserve_recent
    )
    record_compact_checkpoint(
        carryover_metadata,
        checkpoint="microcompact_end",
        trigger=trigger,
        message_count=len(microcompacted),
        token_count=estimate_conversation_tokens(microcompacted),
        details={"tokens_freed": tokens_freed},
    )

    post_util = utilisation(microcompacted, capabilities)
    microcompact_sufficient = post_util < threshold
    if microcompact_sufficient or summariser is None:
        result = _build_microcompact_result(
            microcompacted,
            trigger=trigger,
            preserve_recent=preserve_recent,
            carryover_metadata=carryover_metadata,
            pre_compact_message_count=len(messages),
            pre_compact_token_count=estimate_conversation_tokens(messages),
        )
        state.compacted_this_turn = True
        rebuilt = build_post_compact_messages(result)
        _emit(
            event_sink,
            ContextCompactionCompleted(
                tier="microcompact",
                preserved_attachments=len(result.attachments),
                resulting_utilisation=utilisation(rebuilt, capabilities),
            ),
        )
        _store_compact_result(state, rebuilt)
        return rebuilt, result

    older, newer = split_preserving_tool_pairs(
        microcompacted, preserve_recent=preserve_recent
    )
    try:
        summary_messages = await _invoke_summariser(summariser, older)
    except Exception as exc:
        state.consecutive_failures += 1
        record_compact_checkpoint(
            carryover_metadata,
            checkpoint=f"{trigger}_failed",
            trigger=trigger,
            message_count=len(microcompacted),
            token_count=estimate_conversation_tokens(microcompacted),
            details={
                "reason": str(exc),
                "consecutive_failures": state.consecutive_failures,
            },
        )
        return messages, None

    return _finalize_full_tier(
        messages,
        microcompacted=microcompacted,
        newer=newer,
        summary_messages=summary_messages,
        capabilities=capabilities,
        state=state,
        trigger=trigger,
        carryover_metadata=carryover_metadata,
        event_sink=event_sink,
        working_dir=working_dir,
    )


def _run_full_tier_sync(
    messages: list[ConversationMessage],
    *,
    microcompacted: list[ConversationMessage],
    capabilities: ProviderCapabilities | None,
    state: AutoCompactState,
    trigger: CompactTrigger,
    preserve_recent: int,
    summariser: SummariserFn,
    carryover_metadata: dict[str, Any] | None,
    event_sink: EventSink | None,
    pre_compact_message_count: int,
    pre_compact_token_count: int,
    working_dir: Path | None,
) -> tuple[list[ConversationMessage], CompactionResult | None]:
    older, newer = split_preserving_tool_pairs(
        microcompacted, preserve_recent=preserve_recent
    )
    try:
        summary_messages = summariser(older)
        if inspect.isawaitable(summary_messages):
            raise TypeError(
                "async summariser requires auto_compact_if_needed_async"
            )
    except Exception as exc:
        state.consecutive_failures += 1
        record_compact_checkpoint(
            carryover_metadata,
            checkpoint=f"{trigger}_failed",
            trigger=trigger,
            message_count=len(microcompacted),
            token_count=estimate_conversation_tokens(microcompacted),
            details={
                "reason": str(exc),
                "consecutive_failures": state.consecutive_failures,
            },
        )
        return messages, None

    return _finalize_full_tier(
        messages,
        microcompacted=microcompacted,
        newer=newer,
        summary_messages=summary_messages,
        capabilities=capabilities,
        state=state,
        trigger=trigger,
        carryover_metadata=carryover_metadata,
        event_sink=event_sink,
        working_dir=working_dir,
    )


def _finalize_full_tier(
    messages: list[ConversationMessage],
    *,
    microcompacted: list[ConversationMessage],
    newer: list[ConversationMessage],
    summary_messages: list[ConversationMessage],
    capabilities: ProviderCapabilities | None,
    state: AutoCompactState,
    trigger: CompactTrigger,
    carryover_metadata: dict[str, Any] | None,
    event_sink: EventSink | None,
    working_dir: Path | None,
) -> tuple[list[ConversationMessage], CompactionResult | None]:
    attachments = build_compact_attachments(carryover_metadata or {})
    boundary = create_compact_boundary_message(
        {
            "trigger": trigger,
            "tier": "full",
            "pre_compact_message_count": len(messages),
            "pre_compact_token_count": estimate_conversation_tokens(messages),
        }
    )
    result = CompactionResult(
        trigger=trigger,
        tier="full",
        boundary_marker=boundary,
        summary_messages=list(summary_messages),
        messages_to_keep=list(newer),
        attachments=attachments,
        metadata={"tier": "full"},
    )
    state.compacted_this_turn = True
    state.consecutive_failures = 0

    rebuilt = build_post_compact_messages(result)
    rebuilt = inject_todo_snapshot(rebuilt, working_dir)
    _emit(
        event_sink,
        ContextCompactionCompleted(
            tier="full",
            preserved_attachments=len(result.attachments),
            resulting_utilisation=utilisation(rebuilt, capabilities),
        ),
    )
    _store_compact_result(state, rebuilt)
    return rebuilt, result


# --- reactive (PTL) ----------------------------------------------------------


def react_to_ptl(
    messages: list[ConversationMessage],
    *,
    already_attempted: bool,
    preserve_recent: int = DEFAULT_KEEP_RECENT,
) -> tuple[list[ConversationMessage], bool]:
    """Shrink messages in response to a prompt-too-long provider error.

    Returns ``(new_messages, did_shrink)``. A second call within the same
    provider attempt must pass ``already_attempted=True`` and will no-op
    (Spec 04 acceptance #3).
    """
    if already_attempted:
        return messages, False

    collapsed = try_context_collapse(messages, preserve_recent=preserve_recent)
    if collapsed is not None:
        return collapsed, True

    truncated = truncate_head_for_ptl_retry(messages)
    if truncated is not None:
        return truncated, True

    return messages, False


# --- internals ---------------------------------------------------------------


def _emit(sink: EventSink | None, event: ContextEvent) -> None:
    if sink is not None:
        sink(event)


def _build_microcompact_result(
    messages: Sequence[ConversationMessage],
    *,
    trigger: CompactTrigger,
    preserve_recent: int,
    carryover_metadata: dict[str, Any] | None,
    pre_compact_message_count: int,
    pre_compact_token_count: int,
) -> CompactionResult:
    """Wrap a microcompact-only outcome in a CompactionResult.

    The engine still receives attachments + a boundary marker even when
    the LLM tier didn't run, so the post-compact rebuild path is uniform.
    """
    _ = preserve_recent  # boundary placement is downstream; field documented for symmetry
    boundary = create_compact_boundary_message(
        {
            "trigger": trigger,
            "tier": "microcompact",
            "pre_compact_message_count": pre_compact_message_count,
            "pre_compact_token_count": pre_compact_token_count,
        }
    )
    attachments = build_compact_attachments(carryover_metadata or {})
    return CompactionResult(
        trigger=trigger,
        tier="microcompact",
        boundary_marker=boundary,
        summary_messages=[],
        messages_to_keep=list(messages),
        attachments=attachments,
        metadata={"tier": "microcompact"},
    )


__all__: list[str] = [
    "AutoCompactState",
    "EventSink",
    "SummariserFn",
    "auto_compact_if_needed",
    "auto_compact_if_needed_async",
    "begin_turn",
    "react_to_ptl",
]
