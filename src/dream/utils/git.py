"""Single thin wrapper around the git CLI.

Centralised here so the subprocess usage lives in exactly one auditable place.
It is safe by construction — a fixed ``git`` executable invoked with a list argv
(no shell, no command assembled from untrusted strings) — which is why the bandit
subprocess findings are suppressed only on this one function.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - git CLI wrapper; usage is shell-free and argv-fixed
from collections.abc import Mapping
from pathlib import Path

__all__ = ["run_git"]

_TIMEOUT_EXIT = 124  # GNU timeout convention


def run_git(
    args: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> tuple[int, str, str]:
    """Run ``git <args>`` in ``cwd``; return ``(returncode, stdout, stderr)``.

    ``env`` replaces the process environment when given (callers that need
    overlays should pass a full mapping). Shadow checkpoints use this to set
    ``GIT_DIR`` / ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` without leaking
    subprocess into other modules.

    Timeouts never raise: they return ``(124, captured_stdout, reason)`` so
    callers keep the established tuple contract.
    """
    merged = dict(env) if env is not None else {**os.environ}
    merged.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        proc = subprocess.run(  # nosec B603 B607 - fixed argv, no shell, no untrusted command
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env=merged,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _as_text(exc.stdout)
        limit = f"{timeout:g}s" if timeout is not None else "unset"
        return (
            _TIMEOUT_EXIT,
            stdout.strip(),
            f"git timed out after {limit}: git {' '.join(args)}",
        )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else value.decode("utf-8", "replace")
