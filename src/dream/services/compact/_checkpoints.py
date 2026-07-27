"""Recoverable compaction checkpoints (Spec 04 #8).

Appends a structured checkpoint trail to the carryover metadata so a crashed
compaction can be reconstructed from the last recorded boundary.
"""

from __future__ import annotations

from typing import Any

from dream.services.compact._carryover_state import CarryoverMetadata, CompactCheckpointRecord
from dream.services.context_log import CompactTrigger


def record_compact_checkpoint(
    carryover_metadata: CarryoverMetadata | None,
    *,
    checkpoint: str,
    trigger: CompactTrigger,
    message_count: int,
    token_count: int,
    attempt: int | None = None,
    details: dict[str, Any] | None = None,
) -> CompactCheckpointRecord:
    """Append a structured checkpoint and stamp it as ``compact_last``.

    **In-place contract (intentional):** when ``carryover_metadata`` is not
    None this mutates it so the orchestrator can thread one object through
    every checkpoint of a compaction. Pass ``None`` to build a payload without
    recording it anywhere.
    """
    record = CompactCheckpointRecord(
        checkpoint=checkpoint,
        trigger=trigger,
        message_count=message_count,
        token_count=token_count,
        attempt=attempt,
        details=dict(details or {}),
    )
    if carryover_metadata is not None:
        carryover_metadata.record_checkpoint(record)
    return record


__all__ = ["record_compact_checkpoint"]
