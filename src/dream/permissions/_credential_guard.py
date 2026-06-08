"""Non-disableable credential-path guard (Spec 13A).

The single most important protection: a fixed, non-removable set of patterns
matching user/system secret locations and the harness's own credential store.
Operators may ADD patterns (via ``Policy.credential_extra``) but can never
remove a built-in or disable the guard. The checker applies it to every target
path of every tool call — reads and writes alike — so an employee can neither
exfiltrate nor tamper with secrets.

Repo-local secrets (``.env``, ``*.key``) are deliberately NOT built in: the
guard blocks reads too and cannot be escaped, so hard-blocking them would break
legitimate repo workflows. They are covered by the session-start threat scan
(13B); an operator who wants them hard-guarded adds them via ``credential_extra``.

Pattern grammar: ``~`` is the running user's home; ``*`` matches within one path
segment; ``**/`` matches any number of leading directories (including none); a
trailing ``/**`` also matches the directory itself.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Fixed, non-removable credential patterns.
BUILTIN_CREDENTIAL_PATTERNS: tuple[str, ...] = (
    "~/.ssh/**",
    "~/.aws/**",
    "~/.gnupg/**",
    "~/.config/gcloud/**",
    "~/.config/gh/hosts.yml",
    "~/.kube/config",
    "~/.docker/config.json",
    "~/.netrc",
    "**/id_rsa",
    "**/id_ed25519",
    "**/*.pem",
    "**/.harness/mcp-credentials.toml",
)


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a credential glob into an anchored regex (used with fullmatch)."""
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


_BUILTIN_REGEXES: tuple[re.Pattern[str], ...] = tuple(
    _glob_to_regex(p) for p in BUILTIN_CREDENTIAL_PATTERNS
)


def _candidates(path: Path, cwd: Path) -> tuple[str, ...]:
    """Both forms of ``path`` to match against: lexical and symlink-resolved.

    The *lexical* form (``..``/``.`` collapsed, symlinks NOT followed) catches
    direct access to a credential path even when it lives behind a symlinked
    parent (e.g. a dotfiles ``~/.config`` link). The *resolved* form catches a
    symlink that points *into* a credential location. Either match blocks.
    """
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    lexical = Path(os.path.normpath(candidate.as_posix())).as_posix()
    try:
        resolved = candidate.resolve(strict=False).as_posix()
    except OSError:
        resolved = lexical
    return (lexical, resolved) if resolved != lexical else (lexical,)


def is_credential_path(path: Path, cwd: Path, extra: tuple[str, ...] = ()) -> bool:
    """True if ``path`` matches a built-in or operator-added credential pattern.

    ``extra`` extends the built-ins; it can never remove one. Both the lexical
    and the symlink-resolved forms of ``path`` are checked, so neither a
    symlinked parent nor a symlink *into* a credential dir can dodge the guard.
    """
    forms = _candidates(path, cwd)
    regexes = _BUILTIN_REGEXES + tuple(_glob_to_regex(p) for p in extra)
    return any(rx.fullmatch(form) is not None for rx in regexes for form in forms)
