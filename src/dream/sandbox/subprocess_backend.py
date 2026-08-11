"""Subprocess sandbox backend — opt-in via ``backend = "subprocess"`` (spec 13B).

Docker is the default execution backend; this adapter is selected when
operators set ``backend = "subprocess"`` in ``.harness/sandbox.toml``, or
when Docker is unavailable and ``fail_if_unavailable`` is false (soft
degrade). Commands run as the harness user in the working directory, with
an explicit environment when one is given. Each command gets its own
process session so a timeout kills the whole tree, not just the shell.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Mapping
from pathlib import Path

from dream.sandbox._adapter import SandboxResult

__all__ = ["SubprocessSandbox"]

_DEFAULT_TIMEOUT_SECONDS = 300.0


class SubprocessSandbox:
    """Run commands as local subprocesses with tree-kill timeouts."""

    async def run(
        self,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> SandboxResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=dict(env) if env is not None else None,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return SandboxResult(returncode=None, stderr=f"spawn failed: {exc}")

        try:
            async with asyncio.timeout(timeout_seconds):
                stdout, stderr = await proc.communicate()
        except TimeoutError:
            await _kill_tree(proc)
            return SandboxResult(
                returncode=None,
                stderr=f"timed out after {timeout_seconds}s",
                timed_out=True,
            )
        return SandboxResult(
            returncode=proc.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    killpg = getattr(os, "killpg", None)
    if killpg is not None and proc.pid is not None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            killpg(os.getpgid(proc.pid), signal.SIGKILL)
    else:  # pragma: no cover - non-POSIX fallback
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(Exception):
        await proc.wait()
