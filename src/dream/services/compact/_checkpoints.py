"""Recoverable compaction checkpoints (Spec 04 #8).

Appends a structured checkpoint trail to the carryover metadata so a crashed
compaction can be reconstructed from the last recorded boundary.
"""

from __future__ import annotations

from typing import Any

from dream.services.context_log import CompactTrigger


def record_compact_checkpoint(
    carryover_metadata: dict[str, Any] | None,
    *,
    checkpoint: str,
    trigger: CompactTrigger,
    message_count: int,
    token_count: int,
    attempt: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a structured checkpoint payload and stamp it as ``compact_last``.

    **In-place contract (intentional):** when ``carryover_metadata`` is not
    None this MUTATES it — appending the payload to its ``compact_checkpoints``
    list and overwriting ``compact_last`` — so the orchestrator can thread one
    metadata dict through every checkpoint of a compaction and recover the full
    trail from it. The same payload is also returned for callers that only want
    the value. Pass ``None`` to build a payload without recording it anywhere.
    """
    payload: dict[str, Any] = {
        "checkpoint": checkpoint,
        "trigger": trigger,
        "message_count": message_count,
        "token_count": token_count,
    }
    if attempt is not None:
        payload["attempt"] = attempt
    if details:
        payload.update(details)
    if carryover_metadata is not None:
        checkpoints = carryover_metadata.setdefault("compact_checkpoints", [])
        if isinstance(checkpoints, list):
            checkpoints.append(payload)
        carryover_metadata["compact_last"] = payload
    return payload


__all__ = ["record_compact_checkpoint"]
