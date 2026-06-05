"""``QueryContext`` + ``run_query`` — the streaming act-loop (Spec 03 stage 2).

``run_query`` is the engine's bounded act-loop. It drives a model turn,
dispatches every tool the assistant requested, appends the matching
``ToolResultBlock``s as a single user message (the tool-call atom), and
re-enters the model until the assistant returns no tool calls or
``max_turns`` is reached.

Two collaborators are abstracted as narrow internal Protocols:

- ``TurnStreamer`` — yields a single model turn's events. The cross-repo
  ``Provider`` Protocol (``dream.contracts.provider``) is adapted to this
  shape at the engine boundary; tests substitute a small fake.
- ``ToolDispatcher`` — runs a tool by name. The real implementation in a
  later stage will wrap the ``#05`` tool catalogue + permission checker;
  tests substitute a small fake.

Lifecycle decisions in this loop are made *only* from event types and
``ContentBlock`` shapes — never from parsing assistant prose (acceptance #7).
The loop is bounded by ``max_turns`` (acceptance #6).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from dream.engine._events import (
    AssistantTurnComplete,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    ToolResultBlock,
    ToolUseBlock,
)


class TurnStreamer(Protocol):
    """Streams events for a single model turn.

    Implementations MUST end the iterator with exactly one
    ``AssistantTurnComplete`` carrying the final assembled blocks + usage.
    """

    def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]: ...


class ToolDispatcher(Protocol):
    """Runs a tool by name and returns ``(content, is_error)``.

    The richer ``ToolResult`` from ``dream.contracts.tool`` is reduced to
    this minimal pair here; the FSM-layer dispatcher will collapse the
    richer return to this shape before calling the loop.
    """

    async def dispatch(
        self, name: str, input: dict[str, Any]
    ) -> tuple[str, bool]: ...


@dataclass
class QueryContext:
    """Everything ``run_query`` needs for one bounded act-loop.

    Kept minimal in stage 2; stage 3 will extend it with hook executor,
    permission checker, system prompt, compaction config, etc.
    """

    client: TurnStreamer
    tools: ToolDispatcher
    max_turns: int = 8


async def run_query(
    ctx: QueryContext, messages: list[ConversationMessage]
) -> AsyncIterator[StreamEvent]:
    """Run the act-loop, mutating ``messages`` in place and yielding events.

    Each iteration:
      1. Stream a model turn, re-emitting every event.
      2. Append the assistant message (full block content) to the transcript.
      3. If the assistant emitted no ``ToolUseBlock``s, end the loop.
      4. Otherwise dispatch each tool in order, emitting Started/Completed,
         collect the matching ``ToolResultBlock``s into one user message,
         append it, and re-enter the model.

    The loop is bounded by ``ctx.max_turns`` and terminates cleanly when the
    bound is reached — never infinite.
    """
    for _ in range(ctx.max_turns):
        complete: AssistantTurnComplete | None = None
        async for ev in ctx.client.stream_turn(messages):
            yield ev
            if isinstance(ev, AssistantTurnComplete):
                complete = ev
        if complete is None:
            return

        messages.append(
            ConversationMessage(role="assistant", content=list(complete.blocks))
        )

        tool_uses: list[ToolUseBlock] = [
            b for b in complete.blocks if isinstance(b, ToolUseBlock)
        ]
        if not tool_uses:
            return

        results: list[ContentBlock] = []
        for tu in tool_uses:
            yield ToolExecutionStarted(tool=tu.name, id=tu.id, input=dict(tu.input))
            try:
                content, is_error = await ctx.tools.dispatch(tu.name, dict(tu.input))
            except Exception as exc:  # never crash the loop on a tool failure
                content, is_error = f"tool error: {exc}", True
            yield ToolExecutionCompleted(
                tool=tu.name, id=tu.id, result=content, is_error=is_error
            )
            results.append(
                ToolResultBlock(tool_use_id=tu.id, content=content, is_error=is_error)
            )

        messages.append(ConversationMessage(role="user", content=results))


__all__ = ["QueryContext", "ToolDispatcher", "TurnStreamer", "run_query"]
