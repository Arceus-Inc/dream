"""SpawnOutcome — the result returned by a spawn closure.

The closure calls harness.run_role and translates its RunRoleResult into
this typed value object so the tool layer never imports runner internals
directly. Frozen dataclass for immutability and hashability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dream.session import SessionCost


@dataclass(frozen=True)
class SpawnOutcome:
    """The final product of one spawn call.

    ``final_text`` is the child's concatenated assistant text, returned
    verbatim to the parent model as the tool result. ``session_id``
    links the child's JSONL trace to the parent's. ``cost`` carries the
    child's token counters. ``status`` is "completed" on success.
    ``unknown_tools`` names any requested tool names that were not in the
    registry; reported in structured output rather than silently dropped.
    """

    final_text: str
    session_id: str
    cost: SessionCost
    status: str = "completed"
    unknown_tools: list[str] = field(default_factory=list)


__all__ = ["SpawnOutcome"]
