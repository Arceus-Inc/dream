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

from pathlib import Path

from dream.permissions._globs import glob_to_regex
from dream.utils.paths import canonical_path_forms

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
    "~/.npmrc",
    "~/.pypirc",
    "~/.config/pip/pip.conf",
    "~/.gradle/gradle.properties",
    "**/id_rsa",
    "**/id_ed25519",
    "**/*.pem",
    "**/.harness/credentials.toml",
    "**/.harness/mcp-credentials.toml",
    # Governance-policy inputs (Spec 13B/13C): every file the permission
    # pipeline reads policy from. An agent editing these edits its own
    # permissions — observed live: a session denied by the trust ramp used
    # write_file to self-promote its tools in tool-tier-overrides.toml.
    # Only the operator, outside a session, may change them.
    "**/.harness/sandbox.toml",
    "**/.harness/tool-tier-overrides.toml",
    "**/.harness/net-allowlist.toml",
    "**/.harness/plugins-enabled.toml",
    "**/.harness/lurkr-ignore.toml",
)


_BUILTIN_REGEXES = tuple(glob_to_regex(p) for p in BUILTIN_CREDENTIAL_PATTERNS)


def _candidates(path: Path, cwd: Path) -> tuple[str, ...]:
    """Both forms of ``path`` to match against: lexical and symlink-resolved.

    The *lexical* form (``..``/``.`` collapsed, symlinks NOT followed) catches
    direct access to a credential path even when it lives behind a symlinked
    parent (e.g. a dotfiles ``~/.config`` link). The *resolved* form catches a
    symlink that points *into* a credential location. Either match blocks.
    """
    canonical = canonical_path_forms(path, cwd)
    lexical = canonical.lexical.as_posix()
    resolved = canonical.resolved.as_posix() if canonical.resolved is not None else lexical
    return (lexical, resolved) if resolved != lexical else (lexical,)


def is_credential_path(path: Path, cwd: Path, extra: tuple[str, ...] = ()) -> bool:
    """True if ``path`` matches a built-in or operator-added credential pattern.

    ``extra`` extends the built-ins; it can never remove one. Both the lexical
    and the symlink-resolved forms of ``path`` are checked, so neither a
    symlinked parent nor a symlink *into* a credential dir can dodge the guard.
    """
    forms = _candidates(path, cwd)
    regexes = _BUILTIN_REGEXES + tuple(glob_to_regex(p) for p in extra)
    return any(rx.fullmatch(form) is not None for rx in regexes for form in forms)
