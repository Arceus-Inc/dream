"""Memory substrate — the read side of durable memory (spec 11; spec 15 P4).

Markdown records with YAML frontmatter under a per-project directory
(:func:`project_memory_dir`). :class:`FileMemoryStore` implements the
cross-repo :class:`dream.contracts.memory.MemoryStore` protocol.
Curation and self-evolution (the spec 11 *brain*) deliberately live
outside the SDK — a ``MemoryWriter`` in the business repo (Model A).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from dream.contracts.memory import MemoryRecord, MemoryScope, MemoryType
from dream.memory._catalogue import memory_description, render_memory_catalogue
from dream.memory._context import (
    MEMORY_CONTEXT_KEY,
    MemoryContext,
    put_memory_context,
    read_memory_context,
)
from dream.memory._paths import project_memory_dir
from dream.memory._proposals import (
    InvalidSlugError,
    proposals_dir,
    validate_slug,
    write_proposal,
)
from dream.memory._scan import scan_memory_dir
from dream.memory._search import search_records
from dream.memory._task_context import (
    TASK_MEMORY_CONTEXT_KEY,
    TaskMemoryContext,
    put_task_memory_context,
    read_task_memory_context,
)
from dream.memory._working import (
    DEFAULT_CAP_BYTES,
    CompressionOutcome,
    Compressor,
    WorkingMemory,
)

__all__ = [
    "DEFAULT_CAP_BYTES",
    "MEMORY_CONTEXT_KEY",
    "TASK_MEMORY_CONTEXT_KEY",
    "CompressionOutcome",
    "Compressor",
    "FileMemoryStore",
    "InvalidSlugError",
    "MemoryContext",
    "TaskMemoryContext",
    "WorkingMemory",
    "memory_description",
    "project_memory_dir",
    "proposals_dir",
    "put_memory_context",
    "put_task_memory_context",
    "read_memory_context",
    "read_task_memory_context",
    "render_memory_catalogue",
    "scan_memory_dir",
    "validate_slug",
    "write_proposal",
]


class FileMemoryStore:
    """Read-side :class:`MemoryStore` over one memory directory.

    Scans are per-call (no cache): the store reads at session start and
    on explicit tool calls, and the directory is owned by an external
    curator that may rewrite it at any time.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    async def list(
        self,
        *,
        scope: MemoryScope | None = None,
        type: MemoryType | None = None,
    ) -> Sequence[MemoryRecord]:
        records = scan_memory_dir(self._root)
        return [
            r
            for r in records
            if (scope is None or r.scope == scope)
            and (type is None or r.type == type)
        ]

    async def get(self, record_id: str) -> MemoryRecord | None:
        for record in scan_memory_dir(self._root):
            if record.id == record_id:
                return record
        return None

    async def search(self, query: str, *, limit: int = 20) -> Sequence[MemoryRecord]:
        return search_records(scan_memory_dir(self._root), query, limit=limit)
