"""Per-call timeout helper for substrate adapters.

Spec 02 classifies timeouts as *transient* — they re-enter the cooldown ladder
the same way 429s do. This module provides the bare timeout primitive; the
classification + retry logic lives in :mod:`dream.api._retry` (Stage 3).

Kept separate from any specific adapter so the OpenAI, Anthropic, and local
adapters all reach for the same primitive — and so swapping in a different
deadline source (e.g. an OpenTelemetry context's deadline) is a one-file
change.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

DEFAULT_TIMEOUT_SECONDS: float = 60.0
"""Conservative default; operators override per substrate in ``substrates.toml``."""


class SubstrateTimeout(TimeoutError):
    """Raised when a substrate call exceeds its per-call deadline.

    Inherits from :exc:`TimeoutError` so existing ``except TimeoutError`` blocks
    in adapters and the runner classify it correctly out of the box.
    """


@dataclass(frozen=True)
class Deadline:
    """A per-call deadline. Pass to SDKs that accept a ``timeout=`` kwarg."""

    seconds: float

    @classmethod
    def of(cls, seconds: float | None) -> Deadline:
        return cls(seconds=float(seconds) if seconds is not None else DEFAULT_TIMEOUT_SECONDS)


@contextmanager
def call_deadline(seconds: float | None = None) -> Iterator[Deadline]:
    """Yield a :class:`Deadline` for callers that prefer a context-manager shape.

    The deadline isn't enforced by this context manager — enforcement is the
    underlying SDK's job (we pass ``timeout=deadline.seconds`` to it). The
    context manager exists so the runner can stack deadlines / inject test
    doubles without each adapter re-parsing ``None``.
    """
    yield Deadline.of(seconds)


_local = threading.local()


def current_deadline() -> Deadline | None:
    """Return any deadline pushed by an enclosing scope. ``None`` if unset.

    Adapters may consult this to honour an outer deadline without taking one
    as an explicit kwarg. Today only used by tests; the runner will push real
    deadlines in Stage 3.
    """
    return getattr(_local, "deadline", None)


@contextmanager
def push_deadline(deadline: Deadline) -> Iterator[Deadline]:
    """Stack a deadline on the calling thread's local. Pops on exit."""
    prev = getattr(_local, "deadline", None)
    _local.deadline = deadline
    try:
        yield deadline
    finally:
        if prev is None:
            del _local.deadline
        else:
            _local.deadline = prev
