"""``python -m dream.daemon`` — the headless long-running harness (spec 15 P1 §4).

env/config → :func:`dream.build_harness` → :meth:`dream.Runtime.run_forever`
with graceful signal handling (SIGTERM/SIGINT = request stop, drain, exit).

Credentials come from ``DREAM_API_KEY`` / ``DREAM_MODEL`` /
``DREAM_BASE_URL`` (with the REPL's ``DREAM_SMOKE_*`` names accepted as a
fallback so existing setups run unchanged). Exit codes: 0 clean, 2 missing
credentials, 3 boot blocked, 4 another instance holds the runtime lock.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from dream._factory import DEFAULT_BASE_URL, build_harness
from dream.runtime import (
    Runtime,
    RuntimeBootBlockedError,
    RuntimeBusyError,
    RuntimeConfig,
)

__all__ = ["main", "parse_args", "run_daemon"]

EXIT_OK = 0
EXIT_MISSING_ENV = 2
EXIT_BOOT_BLOCKED = 3
EXIT_ALREADY_RUNNING = 4

# (canonical, REPL-compat fallback) per credential.
_ENV_KEYS = {
    "api_key": ("DREAM_API_KEY", "DREAM_SMOKE_API_KEY"),
    "model": ("DREAM_MODEL", "DREAM_SMOKE_MODEL"),
    "base_url": ("DREAM_BASE_URL", "DREAM_SMOKE_BASE_URL"),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m dream.daemon",
        description="Run the dream harness as a long-lived headless process.",
    )
    parser.add_argument(
        "--working-dir",
        type=Path,
        default=Path.cwd(),
        help="repo the harness works in (default: cwd)",
    )
    parser.add_argument(
        "--agent-id",
        default="default",
        help="identity for wake/heartbeat state (default: %(default)s)",
    )
    parser.add_argument(
        "--wake-idle-minutes",
        type=int,
        default=None,
        help="fire a wake cycle after this many idle minutes (default: off)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=8,
        help="per-session turn budget (default: %(default)s)",
    )
    return parser.parse_args(argv)


def _lookup(env: Mapping[str, str], names: tuple[str, str]) -> str | None:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


def _resolve_credentials(
    env: Mapping[str, str],
) -> tuple[str, str, str] | list[str]:
    """Return ``(model, api_key, base_url)`` or the list of missing var names."""
    api_key = _lookup(env, _ENV_KEYS["api_key"])
    model = _lookup(env, _ENV_KEYS["model"])
    base_url = _lookup(env, _ENV_KEYS["base_url"]) or DEFAULT_BASE_URL
    missing = [
        canonical
        for value, (canonical, _) in (
            (api_key, _ENV_KEYS["api_key"]),
            (model, _ENV_KEYS["model"]),
        )
        if value is None
    ]
    if missing:
        return missing
    assert api_key is not None and model is not None
    return model, api_key, base_url


async def run_daemon(
    *,
    working_dir: Path,
    env: Mapping[str, str] | None = None,
    agent_id: str = "default",
    wake_idle_minutes: int | None = None,
    max_turns: int = 8,
    stderr: TextIO | None = None,
    install_signal_handlers: bool = True,
    on_started: Callable[[Runtime], None] | None = None,
) -> int:
    """Construct the harness + runtime and block until stopped.

    ``on_started`` receives the live :class:`Runtime` right after boot —
    the seam tests (and embedders) use to observe or stop the daemon
    without sending real signals.
    """
    err = stderr if stderr is not None else sys.stderr
    resolved_env = env if env is not None else os.environ
    credentials = _resolve_credentials(resolved_env)
    if isinstance(credentials, list):
        err.write(
            "missing required env vars: " + ", ".join(credentials) + "\n"
        )
        return EXIT_MISSING_ENV
    model, api_key, base_url = credentials

    harness = build_harness(
        model=model,
        api_key=api_key,
        base_url=base_url,
        working_dir=working_dir,
        max_turns=max_turns,
        env=resolved_env,
    )
    runtime = Runtime(
        harness,
        RuntimeConfig(agent_id=agent_id, wake_idle_minutes=wake_idle_minutes),
    )
    if install_signal_handlers:  # pragma: no cover - signals untestable in pytest
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, runtime.request_stop)
    try:
        async with runtime:
            if on_started is not None:
                on_started(runtime)
            await runtime.run_forever()
    except RuntimeBootBlockedError as exc:
        err.write(f"blocked: {exc}\n")
        return EXIT_BOOT_BLOCKED
    except RuntimeBusyError as exc:
        err.write(f"already running: {exc}\n")
        return EXIT_ALREADY_RUNNING
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Synchronous CLI entrypoint for ``python -m dream.daemon``."""
    args = parse_args(argv)
    return asyncio.run(
        run_daemon(
            working_dir=args.working_dir,
            agent_id=args.agent_id,
            wake_idle_minutes=args.wake_idle_minutes,
            max_turns=args.max_turns,
        )
    )
