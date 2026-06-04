"""Single thin wrapper around the git CLI.

Centralised here so the subprocess usage lives in exactly one auditable place.
It is safe by construction — a fixed ``git`` executable invoked with a list argv
(no shell, no command assembled from untrusted strings) — which is why the bandit
subprocess findings are suppressed only on this one function.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - git CLI wrapper; usage is shell-free and argv-fixed
from pathlib import Path

__all__ = ["run_git"]


def run_git(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    """Run ``git <args>`` in ``cwd``; return ``(returncode, stdout, stderr)``."""
    proc = subprocess.run(  # nosec B603 B607 - fixed argv, no shell, no untrusted command
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
