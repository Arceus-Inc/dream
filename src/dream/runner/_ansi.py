"""Shared TTY-gated, dependency-free ANSI helpers.

The runner's :class:`~dream.runner._observer.StdioObserver` renders styled
lines to a text stream that is colourised *only* when the stream is a real
terminal. Tests inject ``io.StringIO`` (no ``isatty``) and assert on plain
text, so colour must be opt-in per stream. This module is the single source
of the ANSI constants and the three primitives:

* :func:`use_colour` — TTY gate (with an optional ``NO_COLOR`` honour);
* :func:`c` — wrap a string in a code + reset, or pass through;
* :func:`flatten` — collapse newlines/tabs so a blob renders on one line.
"""

from __future__ import annotations

import os
from typing import TextIO

__all__ = [
    "BLUE",
    "BOLD",
    "CYAN",
    "DIM",
    "GREEN",
    "MAGENTA",
    "RED",
    "RESET",
    "YELLOW",
    "c",
    "flatten",
    "use_colour",
]

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"


def use_colour(stream: TextIO, *, respect_no_color: bool = False) -> bool:
    """Return True only when ``stream`` is a real terminal.

    StringIO / pipes / redirected files return False so test snapshots and
    machine-consumed logs stay plain text. When ``respect_no_color`` is set
    the ``NO_COLOR`` env var forces plain text even on a TTY.
    """
    if respect_no_color and os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def c(code: str, text: str, *, use: bool) -> str:
    """Wrap ``text`` in ``code`` + reset, or return as-is when ``use`` is False."""
    if not use or not code:
        return text
    return f"{code}{text}{RESET}"


def flatten(s: str) -> str:
    """Collapse newlines/tabs so a tool blob renders on one tidy line."""
    return s.replace("\r", "").replace("\n", " ⏎ ").replace("\t", "  ")
