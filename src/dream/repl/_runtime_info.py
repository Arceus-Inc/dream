"""Render a small ``Runtime environment`` block for the system prompt.

The model otherwise has to guess what shell ``task_create`` will hand a
``command`` string to — and guesses ``bash`` on Windows, which cmd.exe then
can't execute. Injecting the detected shell + OS at session-start removes
the guess entirely; the model picks ``powershell`` syntax on Windows and
``bash`` on POSIX without ever being told to.

Pure formatting; no env mutation. Detection reads ``platform`` and
the standard ``SHELL`` / ``COMSPEC`` env vars, both overridable for tests.
"""

from __future__ import annotations

import platform
import sys
from collections.abc import Mapping
from pathlib import Path


def detect_shell(env: Mapping[str, str]) -> str:
    """Return the shell ``create_subprocess_shell`` will invoke a ``command`` with.

    Mirrors the rule asyncio uses on each platform. On POSIX this is **always**
    ``/bin/sh``: ``create_subprocess_shell`` is called without an ``executable``
    in :meth:`dream.tasks.BackgroundTaskManager.create_shell_task`, so Python
    runs the command as ``/bin/sh -c`` and ignores ``$SHELL`` entirely.
    Advertising ``$SHELL`` (e.g. zsh/bash) would mislead the model into emitting
    shell-specific syntax that fails under ``sh``. On Windows the subprocess
    goes through ``%COMSPEC%`` (cmd.exe by default), which we do honour.
    """
    if sys.platform == "win32":
        return env.get("COMSPEC") or "cmd.exe"
    return "/bin/sh"


def render_runtime_info(
    *, env: Mapping[str, str], working_dir: Path
) -> str:
    """Render the ``Runtime environment`` system-prompt block.

    The header line names the block so the model can spot it as authoritative
    grounding rather than chatter. The shell line is the one that matters for
    ``task_create`` ``command=...`` calls.
    """
    shell = detect_shell(env)
    return (
        "Runtime environment\n"
        f"- OS: {platform.system()} ({platform.release()})\n"
        f"- Shell (used by task_create command=...): {shell}\n"
        f"- Python: {platform.python_version()}\n"
        f"- Working directory: {working_dir}\n"
        "When you call task_create with command=..., write the command in the "
        "syntax that shell understands (POSIX sh on Linux/macOS, cmd.exe on "
        "Windows). Use argv=[...] when you need to bypass the shell entirely."
    )


__all__ = ["detect_shell", "render_runtime_info"]
