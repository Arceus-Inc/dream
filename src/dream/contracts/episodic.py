"""Episodic memory contracts — prior-run search seam for ``session_search``.

Siblings (e.g. chorus ``EpisodicStore``) implement :class:`EpisodicStore`.
Dream keeps the Protocol here so the runtime stays free of org/employee deps.
Search-only: no get-by-id drill-down on this seam.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EpisodicRecord:
    """One prior run / beat available to search."""

    run_id: str
    intent: str
    outcome: str
    body: str
    created_at: datetime
    task_id: str = ""
    files_touched: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EpisodicSearchHit:
    """One keyword hit with an optional snippet around the match."""

    record: EpisodicRecord
    snippet: str = ""


@runtime_checkable
class EpisodicStore(Protocol):
    """Read-side prior-run search. Implementations are pluggable."""

    async def search(
        self, query: str, *, limit: int = 5
    ) -> Sequence[EpisodicSearchHit]: ...


__all__ = ["EpisodicRecord", "EpisodicSearchHit", "EpisodicStore"]
