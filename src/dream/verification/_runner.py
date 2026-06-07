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
    verifier = ui_verifier or SkipUiVerifier()
    for path in ui_paths:
        results.append(await verifier.verify(path))
    return VerificationReport(steps=tuple(results))


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
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(Exception):
        await proc.wait()


__all__ = ["run_verification"]
