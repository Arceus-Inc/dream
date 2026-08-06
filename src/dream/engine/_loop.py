"""``QueryContext`` + ``run_query`` — the streaming act-loop (Spec 03 stage 2).

``run_query`` is the engine's bounded act-loop. It drives a model turn,
dispatches every tool the assistant requested, appends the matching
``ToolResultBlock``s as a single user message (the tool-call atom), and
re-enters the model until the assistant returns no tool calls or
``max_turns`` / :class:`IterationBudget` is exhausted.

Two collaborators are abstracted as narrow internal Protocols:

- ``TurnStreamer`` — yields a single model turn's events. The cross-repo
  ``Provider`` Protocol (``dream.contracts.provider``) is adapted to this
  shape at the engine boundary; tests substitute a small fake.
- ``ToolDispatcher`` — runs a tool by name. The real implementation in a
  later stage will wrap the ``#05`` tool catalogue + permission checker;
  tests substitute a small fake.

Lifecycle decisions in this loop are made *only* from event types and
``ContentBlock`` shapes — never from parsing assistant prose (acceptance #7).
The loop is bounded by ``max_turns`` (acceptance #6). Programmatic turns
(``execute_code`` / ``spawn_subagent`` only) refund one iteration so they do
not burn the parent cap the same way a reasoning turn does.
"""

from __future__ import annotations

import contextlib
import copy
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from dream.engine._events import (
    AssistantTurnComplete,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from dream.engine._iteration_budget import IterationBudget, is_programmatic_only
from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.observability._events import llm_call_attrs, tool_call_attrs, tool_result_attrs
from dream.observability._tracer import NoopTracer, Tracer
from dream.services.compact._orchestrator import react_to_ptl
from dream.services.compact._overflow import is_context_length_overflow


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

    async def dispatch(self, name: str, input: dict[str, Any]) -> tuple[str, bool]: ...


@contextlib.asynccontextmanager
async def _managed_turn_stream(
    stream: AsyncIterator[StreamEvent],
) -> AsyncIterator[AsyncIterator[StreamEvent]]:
    if hasattr(stream, "aclose"):
        async with contextlib.aclosing(cast(AsyncGenerator[StreamEvent, None], stream)):
            yield stream
    else:
        yield stream


@dataclass
class QueryContext:
    """Everything ``run_query`` needs for one bounded act-loop.

    Kept minimal in stage 2; stage 3 will extend it with hook executor,
    permission checker, system prompt, compaction config, etc.
    """

    client: TurnStreamer
    tools: ToolDispatcher
    max_turns: int = 8
    tracer: Tracer = field(default_factory=NoopTracer)
    model: str = ""
    system: str = "openai"
    # Spec 04 reactive PTL: when set, a context-overflow provider error on a
    # turn that has not yet yielded events triggers one ``react_to_ptl`` shrink
    # + retry. ``None`` disables (default; session enables when compactor set).
    ptl_preserve_recent: int | None = None
    # Optional pre-built budget. When unset, ``run_query`` mints one from
    # ``max_turns``. Callers that share a budget across retries can pass one.
    iteration_budget: IterationBudget | None = None


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

    The loop is bounded by ``ctx.max_turns`` (via :class:`IterationBudget`) and
    terminates cleanly when the bound is reached — never infinite. Turns whose
    tool set is exclusively programmatic (``execute_code`` / ``spawn_subagent``)
    refund one iteration so nested RPC / spawn work does not burn the parent
    cap the same way a reasoning turn does.
    """
    budget = ctx.iteration_budget or IterationBudget(ctx.max_turns)
    while budget.consume():
        # The llm.call span stays open across this turn's tool dispatch, so the
        # tool.call / tool.result events nest under it (Spec 12a, AC #17).
        # Seed the span at open with model + zero usage, so a turn cancelled
        # mid-stream (timeout/coma) still emits a usable llm.call (model present,
        # zero tokens) rather than a usage-less stub. Real usage overrides below.
        with ctx.tracer.span(
            "llm.call",
            llm_call_attrs(
                system=ctx.system, model=ctx.model, prompt_tokens=0, completion_tokens=0
            ),
        ) as llm_span:
            complete: AssistantTurnComplete | None = None
            ptl_attempted = False
            while True:
                yielded = False
                try:
                    # ``aclosing`` ensures the per-turn provider stream is closed when
                    # this loop is itself closed mid-flight (timeout/coma/cancel),
                    # cascading the ``aclose()`` down to the transport so it can't leak.
                    turn_stream = ctx.client.stream_turn(messages)
                    async with _managed_turn_stream(turn_stream):
                        async for ev in turn_stream:
                            yielded = True
                            yield ev
                            if isinstance(ev, AssistantTurnComplete):
                                complete = ev
                except Exception as exc:
                    # Mid-turn overflow after partial events: do not replay
                    # (Spec 04 / Failover §13). CancelledError is BaseException —
                    # deliberately not caught here so cooperative cancel propagates.
                    if yielded:
                        raise
                    if (
                        ptl_attempted
                        or ctx.ptl_preserve_recent is None
                        or not is_context_length_overflow(exc)
                    ):
                        raise
                    shrunk, did_shrink = react_to_ptl(
                        messages,
                        already_attempted=False,
                        preserve_recent=ctx.ptl_preserve_recent,
                    )
                    if not did_shrink:
                        raise
                    messages[:] = shrunk
                    ptl_attempted = True
                    yield StatusEvent(message="context overflow: shrunk transcript, retrying once")
                    continue
                break

            if complete is None:
                return

            llm_span.update(
                llm_call_attrs(
                    system=ctx.system,
                    model=ctx.model,
                    prompt_tokens=complete.usage.input_tokens,
                    completion_tokens=complete.usage.output_tokens,
                    cache_read_tokens=complete.usage.cache_read_tokens,
                )
            )
            messages.append(ConversationMessage(role="assistant", content=list(complete.blocks)))

            tool_uses: list[ToolUseBlock] = [
                b for b in complete.blocks if isinstance(b, ToolUseBlock)
            ]
            if not tool_uses:
                return

            results: list[ContentBlock] = []
            for tu in tool_uses:
                # Deep copy: the transcript block, the emitted event payload, and
                # the dispatch argument must each be isolated, so an in-place
                # mutation by a dispatcher can never rewrite history or an
                # already-emitted event.
                yield ToolExecutionStarted(tool=tu.name, id=tu.id, input=copy.deepcopy(tu.input))
                ctx.tracer.event("tool.call", tool_call_attrs(tool_name=tu.name))
                try:
                    content, is_error = await ctx.tools.dispatch(tu.name, copy.deepcopy(tu.input))
                except Exception as exc:  # never crash the loop on a tool failure
                    # A *raised* exception is an infrastructure failure (sandbox,
                    # permissions, MCP transport) — not a tool-logic result a tool
                    # would return via ``is_error=True``. Keep the real detail on
                    # the observability side-channel (the event), but never leak
                    # engine internals into the transcript the model re-reads:
                    # send it a generic, non-revealing failure marker instead.
                    detail = f"{type(exc).__name__}: {exc}"
                    yield ToolExecutionCompleted(
                        tool=tu.name, id=tu.id, result=detail, is_error=True
                    )
                    ctx.tracer.event(
                        "tool.result", tool_result_attrs(tool_name=tu.name, is_error=True)
                    )
                    results.append(
                        ToolResultBlock(
                            tool_use_id=tu.id,
                            content=f"tool {tu.name!r} failed to execute",
                            is_error=True,
                        )
                    )
                    continue
                yield ToolExecutionCompleted(
                    tool=tu.name, id=tu.id, result=content, is_error=is_error
                )
                ctx.tracer.event(
                    "tool.result", tool_result_attrs(tool_name=tu.name, is_error=is_error)
                )
                results.append(
                    ToolResultBlock(tool_use_id=tu.id, content=content, is_error=is_error)
                )

            messages.append(ConversationMessage(role="user", content=results))
            if is_programmatic_only(tu.name for tu in tool_uses):
                budget.refund()


__all__ = ["QueryContext", "ToolDispatcher", "TurnStreamer", "run_query"]
