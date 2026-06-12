"""Task memory — the working-memory scratchpad (spec 11a; spec 11 §"working").

dream owns exactly one *writable* memory tier: a single free-form
``working-memory.md`` that lives and dies with the worktree (``#02``). It is the
agent's own cognition mid-task — what it figured out, open questions, things
worth remembering later — so it is written freely and never gated by the sandbox
tier.

Two invariants live here:

- **50 KB cap.** Past the cap the runtime may compress the file in place. The
  original is appended to ``working-memory.history.md`` (a mortal debug aid) and
  the compressed text replaces the file atomically. The compressor is *injected*
  (:data:`Compressor`) so the SDK hard-codes no provider and the mechanism is
  pure and unit-testable.
- **Garbage rollback.** If the compressor returns empty or larger-than-original
  output it is treated as garbage: the file is left untouched and the outcome
  says ``rolled_back``. A bad compression never destroys the agent's notes.

Promotion of a working-memory insight to durable memory is *not* done here — that
is lattice's slower clock. dream only emits candidates through
:mod:`dream.memory._proposals`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dream.utils.fs import atomic_write_text

__all__ = [
    "DEFAULT_CAP_BYTES",
    "CompressionOutcome",
    "Compressor",
    "WorkingMemory",
]

DEFAULT_CAP_BYTES = 50_000
"""The working-memory size cap (spec 11 decision #9). Past it, compression runs."""

Compressor = Callable[[str], Awaitable[str]]
"""An async ``str -> str`` callback that compresses working memory.

Injected by the runtime (it owns the model wiring); the SDK never constructs
one, which keeps :meth:`WorkingMemory.maybe_compress` pure and testable.
"""

CompressionStatus = Literal["compressed", "rolled_back", "skipped"]


@dataclass(frozen=True)
class CompressionOutcome:
    """The result of a :meth:`WorkingMemory.maybe_compress` call.

    Doubles as the ``working_memory.compressed`` event payload the runtime logs:
    ``status`` plus the byte counts are everything an observer needs without
    reading the file.
    """

    status: CompressionStatus
    before_bytes: int
    after_bytes: int
    reason: str


class WorkingMemory:
    """The per-task working-memory file under the worktree sidecar.

    Bound to one ``working-memory.md`` path. All writes go through
    :func:`dream.utils.fs.atomic_write_text` (spec 01 invariant). Reads of an
    absent file return ``""`` — a fresh task has no notes yet, which is not an
    error.
    """

    def __init__(self, path: Path, *, cap_bytes: int = DEFAULT_CAP_BYTES) -> None:
        self._path = Path(path)
        self._cap_bytes = cap_bytes

    @property
    def path(self) -> Path:
        """The working-memory file path."""
        return self._path

    @property
    def history_path(self) -> Path:
        """The append-only pre-compression history alongside the file."""
        return self._path.with_name(f"{self._path.stem}.history{self._path.suffix}")

    @property
    def cap_bytes(self) -> int:
        """The size cap past which compression is offered."""
        return self._cap_bytes

    def read(self) -> str:
        """Return the current contents, or ``""`` if the file does not exist."""
        if not self._path.exists():
            return ""
        return self._path.read_text(encoding="utf-8")

    def write(self, content: str) -> None:
        """Replace the working-memory file atomically."""
        atomic_write_text(self._path, content)

    def append(self, note: str) -> None:
        """Append ``note`` (on its own line) to the working-memory file."""
        current = self.read()
        if current and not current.endswith("\n"):
            current += "\n"
        atomic_write_text(self._path, current + note + "\n")

    def size_bytes(self) -> int:
        """The current file size in bytes (``0`` if absent)."""
        if not self._path.exists():
            return 0
        return self._path.stat().st_size

    def over_cap(self) -> bool:
        """Whether the file currently exceeds the cap."""
        return self.size_bytes() > self._cap_bytes

    async def maybe_compress(self, compress: Compressor) -> CompressionOutcome:
        """Compress the file in place if it is over the cap.

        Under (or at) the cap is a no-op (``status="skipped"``). Over the cap the
        original is appended to :attr:`history_path` and the compressed text
        replaces the file. A compressor that returns empty or larger-than-
        original output is rolled back — the original file is left untouched
        (``status="rolled_back"``).
        """
        before = self.size_bytes()
        if before <= self._cap_bytes:
            return CompressionOutcome(
                status="skipped",
                before_bytes=before,
                after_bytes=before,
                reason="under cap",
            )

        original = self.read()
        compressed = await compress(original)
        after = len(compressed.encode("utf-8"))
        # Garbage guard: empty (after stripping) or not actually smaller. Either
        # way we keep the original — a bad compression must never lose notes.
        if not compressed.strip() or after >= before:
            return CompressionOutcome(
                status="rolled_back",
                before_bytes=before,
                after_bytes=after,
                reason="compressor returned empty or larger-than-original output",
            )

        self._append_history(original)
        self.write(compressed)
        return CompressionOutcome(
            status="compressed",
            before_bytes=before,
            after_bytes=after,
            reason="compressed in place",
        )

    def _append_history(self, original: str) -> None:
        existing = (
            self.history_path.read_text(encoding="utf-8")
            if self.history_path.exists()
            else ""
        )
        stamp = datetime.now(tz=UTC).isoformat()
        block = (
            f"<!-- compressed {stamp} "
            f"({len(original.encode('utf-8'))} bytes) -->\n{original}\n"
        )
        atomic_write_text(self.history_path, existing + block)
