"""Internal ``StreamEvent`` union — the typed output of ``run_query``.

The engine's act-loop yields events of this taxonomy instead of raw provider
output so the session FSM, hook bus, cost tracker, and UI can react
*structurally*, never by parsing assistant prose (Spec 03 acceptance #7).

This is intentionally separate from the cross-repo ``ProviderEvent`` in
``dream.contracts.provider``: providers expose ``{type: str, data: dict}``
events for substrate flexibility; the engine translates those into this
typed union before they reach the FSM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dream.engine._cost import UsageSnapshot
from dream.engine._messages import ContentBlock


@dataclass(frozen=True)
class AssistantTextDelta:
    """Incremental text chunk from the assistant's current turn."""

    text: str


@dataclass(frozen=True)
class AssistantTurnComplete:
    """Terminates a model turn: carries the full assembled blocks + usage.

    ``blocks`` is the canonical content the engine appends to the transcript;
    ``usage`` feeds the ``CostTracker`` and the per-turn record.
    """

    blocks: list[ContentBlock]
    usage: UsageSnapshot


@dataclass(frozen=True)
class ToolExecutionStarted:
    """A tool dispatch began. Fires once per ``ToolUseBlock`` in the turn."""

    tool: str
    id: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolExecutionCompleted:
    """A tool dispatch finished — successfully or with an error result."""

    tool: str
    id: str
    result: str
    is_error: bool = False


@dataclass(frozen=True)
class StatusEvent:
    """A non-fatal status update from the engine (reconnects, retries, etc.)."""

    message: str


@dataclass(frozen=True)
class CompactProgressEvent:
    """Compaction progress from ``#04`` surfaced through the act-loop stream."""

    pct: float
    message: str = ""


@dataclass(frozen=True)
class CompactionDoneEvent:
    """A compaction tier ran successfully between turns.

    Surfaces the Spec 04 orchestrator outcome on the engine's event
    stream so the public ``Session`` can translate it to ``events.Compacted``
    and the REPL can render a banner. ``removed_messages`` is the
    pre/post message-count delta (zero for microcompact, which preserves
    structure); ``freed_tokens`` is the pre/post token-estimate delta.
    """

    tier: str
    removed_messages: int
    freed_tokens: int
    resulting_utilisation: float


@dataclass(frozen=True)
class ErrorEvent:
    """An error the engine wants visible. ``recoverable`` flags whether the loop continues."""

    message: str
    recoverable: bool = False


StreamEvent = (
    AssistantTextDelta
    | AssistantTurnComplete
    | ToolExecutionStarted
    | ToolExecutionCompleted
    | StatusEvent
    | CompactProgressEvent
    | CompactionDoneEvent
    | ErrorEvent
)


__all__ = [
    "AssistantTextDelta",
    "AssistantTurnComplete",
    "CompactProgressEvent",
    "CompactionDoneEvent",
    "ErrorEvent",
    "StatusEvent",
    "StreamEvent",
    "ToolExecutionCompleted",
    "ToolExecutionStarted",
]
