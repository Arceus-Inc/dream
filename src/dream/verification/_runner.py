"""Run declared verification steps as shell commands (Spec 12c).

Each :class:`VerificationStepSpec` is a *shell* command (operator-declared and
trusted — like a Makefile target), run under a per-step timeout. Exit 0 →
``success``, non-zero → ``failed``, spawn failure / timeout → ``error`` (never
crashes the run). UI paths go through the :class:`UiVerifier` seam (default skips).
The runner returns the in-memory report; persistence is the caller's call
(``_report.write_report``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Sequence
from pathlib import Path

from dream.verification._types import (
    RepoVerificationStep,
    VerificationReport,
    VerificationStepSpec,
)
from dream.verification._ui import SkipUiVerifier, UiVerifier

_DEFAULT_TIMEOUT_S = 300.0


async def run_verification(
    steps: Sequence[VerificationStepSpec],
    *,
    cwd: Path,
    timeout_seconds: float = _DEFAULT_TIMEOUT_S,
    ui_paths: Sequence[str] = (),
    ui_verifier: UiVerifier | None = None,
) -> VerificationReport:
    """Run every shell step then every UI path; collect into a report."""
    results: list[RepoVerificationStep] = [
        await _run_step(spec, cwd=cwd, timeout_seconds=timeout_seconds) for spec in steps
    ]
    # Honour any provided verifier — ``is None`` not ``or`` so a (falsy) custom
    # verifier instance is never silently swapped for the skip default.
    verifier = SkipUiVerifier() if ui_verifier is None else ui_verifier
    for path in ui_paths:
        results.append(await _run_ui(verifier, path))
    return VerificationReport(steps=tuple(results))


async def _run_ui(verifier: UiVerifier, path: str) -> RepoVerificationStep:
    """Run one UI verification, turning a verifier crash into an ``error`` step."""
    try:
        return await verifier.verify(path)
    except Exception as exc:
        return RepoVerificationStep(
            command=f"ui:{path}",
            status="error",
            name=path,
            stderr=f"UI verification raised: {exc}",
        )


async def _run_step(
    spec: VerificationStepSpec, *, cwd: Path, timeout_seconds: float
) -> RepoVerificationStep:
    try:
        proc = await asyncio.create_subprocess_shell(
            spec.command,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own session/process group so a timeout can terminate the whole tree
            # (shell + children/grandchildren), not just the top-level shell PID.
            start_new_session=True,
        )
    except OSError as exc:
        return RepoVerificationStep(
            command=spec.command, status="error", stderr=f"spawn failed: {exc}", name=spec.name
        )

    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, stderr = await proc.communicate()
    except TimeoutError:
        await _kill(proc)
        return RepoVerificationStep(
            command=spec.command,
            status="error",
            stderr=f"timed out after {timeout_seconds}s",
            name=spec.name,
        )

    returncode = proc.returncode
    return RepoVerificationStep(
        command=spec.command,
        status="success" if returncode == 0 else "failed",
        returncode=returncode,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        name=spec.name,
    )


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Terminate the whole process group so timed-out children don't leak.

    The step ran with ``start_new_session=True``, so the child is a group leader
    whose pid equals the group id; ``killpg`` reaps the shell plus any
    descendants. Falls back to a plain ``kill`` where process groups are
    unavailable (e.g. Windows) or already gone.
    """
    killpg = getattr(os, "killpg", None)
    if killpg is not None and proc.pid is not None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            killpg(os.getpgid(proc.pid), signal.SIGKILL)
    else:  # pragma: no cover - non-POSIX fallback
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(Exception):
        await proc.wait()


__all__ = ["run_verification"]
