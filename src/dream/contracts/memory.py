"""Memory contracts.

The SDK reads memory at session start. Writes go through a `MemoryWriter`
Protocol so curation can live outside this repo (e.g. `lattice`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class MemoryScope(str, Enum):
    """Visibility scope for a memory record."""

    PRIVATE = "private"
    PROJECT = "project"
    TEAM = "team"
    COMPANY = "company"


class MemoryType(str, Enum):
    """High-level taxonomy of a memory record."""

    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


@dataclass(frozen=True)
class MemoryRecord:
    """A single memory entry loaded from disk or a store."""

    id: str
    scope: MemoryScope
    type: MemoryType
    content: str
    source: Path | None = None
    modified_at: datetime | None = None
    frontmatter: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryDelta:
    """A proposed change to the memory store.

    Used by curators (in `lattice`) to submit auditable diffs rather than
    arbitrary writes.
    """

    target_id: str
    scope: MemoryScope
    operation: str  # "create" | "update" | "delete"
    new_content: str | None = None
    rationale: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MemoryStore(Protocol):
    """Read-side memory API. Implementations are pluggable."""

    async def list(
        self,
        *,
        scope: MemoryScope | None = None,
        type: MemoryType | None = None,
    ) -> Sequence[MemoryRecord]: ...

    async def get(self, record_id: str) -> MemoryRecord | None: ...

    async def search(self, query: str, *, limit: int = 20) -> Sequence[MemoryRecord]: ...


@runtime_checkable
class MemoryWriter(Protocol):
    """Write-side memory API. Implemented outside the SDK (e.g. chorus)."""

    async def apply(self, delta: MemoryDelta) -> MemoryRecord:
        """Apply a delta atomically and return the resulting record."""
        ...

    async def rollback(self, record_id: str, to_version: str) -> MemoryRecord: ...
