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
)


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


def resolve_binary(metadata: dict[str, Any]) -> str | None:
    """Resolve browser-harness executable path, or None if missing."""
    override = metadata.get(BROWSER_RUN_BIN_KEY)
    if isinstance(override, str) and override.strip():
        path = Path(override.strip())
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    env_bin = os.environ.get(BIN_ENV, "").strip()
    if env_bin:
        path = Path(env_bin)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        which = shutil.which(env_bin)
        if which:
            return which
    return shutil.which("browser-harness")


def resolve_cdp(metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(cdp_url, cdp_ws)`` from session metadata or process env."""
    ws = metadata.get(BROWSER_RUN_CDP_WS_KEY)
    if isinstance(ws, str) and ws.strip():
        return None, ws.strip()
    url = metadata.get(BROWSER_RUN_CDP_URL_KEY)
    if isinstance(url, str) and url.strip():
        return url.strip(), None
    env_ws = os.environ.get(CDP_WS_ENV, "").strip()
    if env_ws:
        return None, env_ws
    env_url = os.environ.get(CDP_URL_ENV, "").strip()
    if env_url:
        return env_url, None
    return None, None


def is_disabled(metadata: dict[str, Any]) -> bool:
    """Operator kill switch via session metadata."""
    flag = metadata.get(BROWSER_RUN_DISABLED_KEY)
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str):
        return flag.strip().lower() in {"1", "true", "yes", "on"}
    return os.environ.get("CHORUS_DISABLE_BROWSER_RUN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def build_spawn_config(*, metadata: dict[str, Any], bu_name: str) -> SpawnConfig | str:
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
    if not cdp_url and not cdp_ws:
        return (
            "No Chromium CDP endpoint configured. Set "
            f"{CDP_URL_ENV}=http://127.0.0.1:9222 (or {CDP_WS_ENV}) and ensure "
            "Chromium is running with --remote-debugging-port."
        )
    env = dict(os.environ)
    env["BU_NAME"] = bu_name
    # Never cloud — strip keys that would enable Browser Use cloud autospawn.
    env.pop("BROWSER_USE_API_KEY", None)
    env.pop("BU_AUTOSPAWN", None)
    env.pop("BU_BROWSER_ID", None)
    if cdp_ws:
        env["BU_CDP_WS"] = cdp_ws
        env.pop("BU_CDP_URL", None)
    else:
        assert cdp_url is not None
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
        return SpawnResult(
            stdout="",
            stderr=f"failed to spawn browser-harness: {exc}",
            returncode=None,
            timed_out=False,
            cancelled=False,
            duration_seconds=time.monotonic() - t0,
        )

    code_bytes = code.encode("utf-8")
    comm = asyncio.create_task(proc.communicate(input=code_bytes))
    deadline = t0 + timeout_seconds
    try:
        while True:
            if cancel_requested is not None and cancel_requested():
                await _kill(proc)
                _cancel_task(comm)
                return SpawnResult(
                    stdout="",
                    stderr="browser_run cancelled by caller",
                    returncode=None,
                    timed_out=False,
                    cancelled=True,
                    duration_seconds=time.monotonic() - t0,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await _kill(proc)
                _cancel_task(comm)
                return SpawnResult(
                    stdout="",
                    stderr=f"browser_run timed out after {timeout_seconds}s",
                    returncode=None,
                    timed_out=True,
                    cancelled=False,
                    duration_seconds=time.monotonic() - t0,
                )
            done, _ = await asyncio.wait({comm}, timeout=min(0.1, remaining))
            if done:
                stdout_b, stderr_b = comm.result()
                break
    except Exception:
        await _kill(proc)
        _cancel_task(comm)
        raise

    return SpawnResult(
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
        returncode=proc.returncode,
        timed_out=False,
        cancelled=False,
        duration_seconds=time.monotonic() - t0,
    )


async def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
        await proc.wait()


def _cancel_task(task: asyncio.Task[Any]) -> None:
    if not task.done():
        task.cancel()


__all__ = [
    "SpawnConfig",
    "SpawnResult",
    "build_spawn_config",
    "is_disabled",
    "resolve_binary",
    "resolve_cdp",
    "run_browser_harness",
]
