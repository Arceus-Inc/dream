"""Glob -> regex translation shared by the permission components (Spec 13A).

Grammar: ``*`` matches within a single path segment; ``?`` one character;
``**/`` any number of leading directories (including none); a trailing ``/**``
also matches the directory itself; ``**`` elsewhere matches across segments.
A leading ``~`` expands to the running user's home. The compiled regex is
anchored implicitly by using it with ``fullmatch``.
"""

from __future__ import annotations

import re
from pathlib import Path


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a path glob into a regex for use with ``fullmatch``."""
    expanded = Path(pattern).expanduser().as_posix() if pattern.startswith("~") else pattern
    out: list[str] = []
    i, n = 0, len(expanded)
    while i < n:
        if expanded.startswith("/**", i) and i + 3 == n:
            out.append("(?:/.*)?")  # trailing /** : the directory or anything under it
            i += 3
        elif expanded.startswith("**/", i):
            out.append("(?:.*/)?")  # any number of leading directories
            i += 3
        elif expanded.startswith("**", i):
            out.append(".*")
            i += 2
        elif expanded[i] == "*":
            out.append("[^/]*")
            i += 1
        elif expanded[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(expanded[i]))
            i += 1
    return re.compile("".join(out))
