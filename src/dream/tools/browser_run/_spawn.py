"""Resolve binary + CDP endpoint and spawn browser-harness with code on stdin."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dream.tools.browser_run._types import (
    BIN_ENV,
    BROWSER_RUN_BIN_KEY,
    BROWSER_RUN_CDP_URL_KEY,
    BROWSER_RUN_CDP_WS_KEY,
    BROWSER_RUN_DISABLED_KEY,
    CDP_URL_ENV,
    CDP_WS_ENV,
    CLOUD_ENV_KEYS,
    Metadata,
)

_DISABLED_ENV = "CHORUS_DISABLE_BROWSER_RUN"
_TRUTHY_FLAGS = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class SpawnConfig:
    """Resolved spawn inputs (fail-closed if binary or CDP missing)."""

    binary: str
    env: dict[str, str]
    cdp_url: str | None
    cdp_ws: str | None


@dataclass(frozen=True, slots=True)
class SpawnResult:
    """Raw subprocess outcome."""

    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    cancelled: bool
    duration_seconds: float


def _nonempty(value: object) -> str | None:
    """``value`` as a stripped string, or None when it's blank or not a string."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _flag_on(value: object) -> bool:
    """Coerce a bool or a truthy flag string to a boolean (default False)."""
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in _TRUTHY_FLAGS


def _is_executable_file(candidate: str) -> bool:
    return Path(candidate).is_file() and os.access(candidate, os.X_OK)


def resolve_binary(metadata: Metadata) -> str | None:
    """Resolve the browser-harness executable path, or None when absent.

    Priority: the session ``BROWSER_RUN_BIN_KEY`` (an exact executable path), ``BIN_ENV``
    (a path or a name on PATH), then a bare ``browser-harness`` on PATH.
    """
    override = _nonempty(metadata.get(BROWSER_RUN_BIN_KEY))
    if override is not None and _is_executable_file(override):
        return override
    env_bin = _nonempty(os.environ.get(BIN_ENV))
    if env_bin is not None:
        if _is_executable_file(env_bin):
            return env_bin
        if hit := shutil.which(env_bin):
            return hit
    return shutil.which("browser-harness")


def resolve_cdp(metadata: Metadata) -> tuple[str | None, str | None]:
    """Return ``(cdp_url, cdp_ws)``, or ``(None, None)`` when nothing is configured.

    Session values win over env defaults; a websocket wins over a URL at the same level.
    """
    session = (
        _nonempty(metadata.get(BROWSER_RUN_CDP_WS_KEY)),
        _nonempty(metadata.get(BROWSER_RUN_CDP_URL_KEY)),
    )
    env = (_nonempty(os.environ.get(CDP_WS_ENV)), _nonempty(os.environ.get(CDP_URL_ENV)))
    for ws, url in (session, env):
        if ws is not None:
            return None, ws
        if url is not None:
            return url, None
    return None, None


def is_disabled(metadata: Metadata) -> bool:
    """Operator kill switch — the session flag wins; the env flag is the default."""
    flag = metadata.get(BROWSER_RUN_DISABLED_KEY)
    if flag is not None:
        return _flag_on(flag)
    return _flag_on(os.environ.get(_DISABLED_ENV))


def build_spawn_config(*, metadata: Metadata, bu_name: str) -> SpawnConfig | str:
    """Build spawn config, or return a human refusal reason string."""
    if is_disabled(metadata):
        return "browser_run disabled (CHORUS_DISABLE_BROWSER_RUN / session flag)"
    binary = resolve_binary(metadata)
    if binary is None:
        return (
            "browser-harness not found on PATH; install with "
            "`uv tool install --python 3.12 browser-harness` or set "
            f"{BIN_ENV} / {BROWSER_RUN_BIN_KEY}"
        )
    cdp_url, cdp_ws = resolve_cdp(metadata)
    if cdp_url is None and cdp_ws is None:
        return (
            "No Chromium CDP endpoint configured. Set "
            f"{CDP_URL_ENV}=http://127.0.0.1:9222 (or {CDP_WS_ENV}) and ensure "
            "Chromium is running with --remote-debugging-port."
        )
    env = dict(os.environ)
    env["BU_NAME"] = bu_name
    # Never cloud — strip keys that would enable Browser Use cloud autospawn.
    for key in CLOUD_ENV_KEYS:
        env.pop(key, None)
    if cdp_ws is not None:
        env["BU_CDP_WS"] = cdp_ws
        env.pop("BU_CDP_URL", None)
    elif cdp_url is not None:
        env["BU_CDP_URL"] = cdp_url
        env.pop("BU_CDP_WS", None)
    return SpawnConfig(binary=binary, env=env, cdp_url=cdp_url, cdp_ws=cdp_ws)


async def run_browser_harness(
    *,
    config: SpawnConfig,
    code: str,
    timeout_seconds: float,
    cancel_requested: Callable[[], bool] | None = None,
) -> SpawnResult:
    """Spawn browser-harness with ``code`` on stdin; capture stdout/stderr."""
    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            config.binary,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=config.env,
        )
    except OSError as exc:
        return _spawn_result(t0, stderr=f"failed to spawn browser-harness: {exc}")

    comm = asyncio.create_task(proc.communicate(input=code.encode("utf-8")))
    try:
        stdout_b, stderr_b, cancelled, timed_out = await _await_result(
            proc, comm, t0=t0, timeout_seconds=timeout_seconds, cancel_requested=cancel_requested
        )
    finally:
        await _cleanup(proc, comm)

    if cancelled:
        return _spawn_result(t0, stderr="browser_run cancelled by caller", cancelled=True)
    if timed_out:
        return _spawn_result(
            t0, stderr=f"browser_run timed out after {timeout_seconds}s", timed_out=True
        )
    return _spawn_result(
        t0,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
        returncode=proc.returncode,
    )


async def _await_result(
    proc: asyncio.subprocess.Process,
    comm: asyncio.Task[tuple[bytes, bytes]],
    *,
    t0: float,
    timeout_seconds: float,
    cancel_requested: Callable[[], bool] | None,
) -> tuple[bytes, bytes, bool, bool]:
    """Poll for completion, caller cancel, or timeout; kill the process on cancel/timeout."""
    deadline = t0 + timeout_seconds
    while True:
        if cancel_requested is not None and cancel_requested():
            await _kill(proc)
            return b"", b"", True, False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            await _kill(proc)
            return b"", b"", False, True
        done, _ = await asyncio.wait({comm}, timeout=min(0.1, remaining))
        if done:
            return (*comm.result(), False, False)


async def _cleanup(proc: asyncio.subprocess.Process, comm: asyncio.Task[Any]) -> None:
    """Never leak the subprocess or the communicate task, whatever the exit path."""
    if not comm.done():
        comm.cancel()
    if proc.returncode is None:
        await _kill(proc)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
        await proc.wait()


def _spawn_result(
    t0: float,
    *,
    stderr: str,
    stdout: str = "",
    returncode: int | None = None,
    timed_out: bool = False,
    cancelled: bool = False,
) -> SpawnResult:
    return SpawnResult(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        timed_out=timed_out,
        cancelled=cancelled,
        duration_seconds=time.monotonic() - t0,
    )


__all__ = [
    "SpawnConfig",
    "SpawnResult",
    "build_spawn_config",
    "is_disabled",
    "resolve_binary",
    "resolve_cdp",
    "run_browser_harness",
]
