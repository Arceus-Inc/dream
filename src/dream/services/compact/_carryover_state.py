"""Typed compaction carryover — contract fields + checkpoint trail (Spec 04 #5/#8).

Replaces string-keyed ``dict[str, Any]`` threading through the compact stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dream.services.context_log import CompactTrigger

UtilisationRatio = float
"""Fraction of the provider context window in use (0.0-1.0)."""


@dataclass(frozen=True)
class BlockedStepEntry:
    step_id: str
    reason: str


@dataclass(frozen=True)
class CompactCheckpointRecord:
    checkpoint: str
    trigger: CompactTrigger
    message_count: int
    token_count: int
    attempt: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CarryoverMetadata:
    """Mutable continuity state threaded through compaction per session."""

    working_dir: str | None = None
    exec_plan_filename: str | None = None
    exec_plan_current_step: str | None = None
    blocked_steps: list[BlockedStepEntry] = field(default_factory=list)
    failing_tests: list[str] = field(default_factory=list)
    modified_file_paths: list[str] = field(default_factory=list)
    open_hooks: list[str] = field(default_factory=list)
    orientation_brief: str | None = None
    core_beliefs_digest: str | None = None
    house_rules: str | None = None
    previous_summary: str | None = None
    compact_checkpoints: list[CompactCheckpointRecord] = field(default_factory=list)
    compact_last: CompactCheckpointRecord | None = None

    @classmethod
    def for_working_dir(cls, working_dir: str | None) -> CarryoverMetadata:
        return cls(working_dir=working_dir)

    def merge_exec_plan(
        self,
        *,
        filename: str,
        current_step: str | None,
        blocked: list[BlockedStepEntry],
    ) -> None:
        self.exec_plan_filename = filename
        if current_step is not None:
            self.exec_plan_current_step = current_step
        if blocked:
            self.blocked_steps = blocked

    def record_checkpoint(self, record: CompactCheckpointRecord) -> None:
        self.compact_checkpoints.append(record)
        self.compact_last = record


__all__ = [
    "BlockedStepEntry",
    "CarryoverMetadata",
    "CompactCheckpointRecord",
    "UtilisationRatio",
]
