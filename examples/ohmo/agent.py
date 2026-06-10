"""Ohmo — the always-on research agent, as a runnable daemon.

Everything long-running comes from the dream runtime: the single-instance
lock, boot gates, the command channel (``python -m dream.ctl``), the cron
tick loop, the wake-cycle heartbeat, the liveness watchdog, supervised
loops, graceful drain, and one observable events JSONL. This file adds
only the *agent*: the persona, the research tools, the workspace
conventions, and the wake handler that turns heartbeat decisions into
persona sessions.

Run::

    DREAM_API_KEY=... DREAM_MODEL=... python examples/ohmo/agent.py \
        --workspace ~/ohmo-lab --wake-idle-minutes 30

Exit codes mirror ``dream.daemon``: 0 clean, 2 missing credentials,
3 boot blocked, 4 already running.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from dream import (
    Runtime,
    RuntimeBootBlockedError,
    RuntimeBusyError,
    RuntimeConfig,
    build_harness,
)
from dream.events import Error, ToolUseResult, TurnComplete
from dream.harness import Harness
from dream.observability import EventSink
from dream.session import SessionOptions
from dream.tools._registry import ToolSource
from dream.tools.builtin import default_registry
from dream.wake import HeartbeatDecision

if __name__ == "__main__" and not __package__:  # pragma: no cover
    # Allow ``python examples/ohmo/agent.py`` without installing the package.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ohmo.persona import OHMO_HEARTBEAT_PROMPT, OHMO_PERSONA
from ohmo.tools import research_tools

EXIT_OK = 0
EXIT_MISSING_ENV = 2
EXIT_BOOT_BLOCKED = 3
EXIT_ALREADY_RUNNING = 4

_SANDBOX_TOML = """\
# Ohmo's posture: repo writes + the net allowlist tier, so the arxiv_search
# tool (tier 2) is callable. Written once at bootstrap; edit freely.
tier = "repo-write+net-allowlist"
"""

_SEED_INDEX = "# Research briefs\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ohmo",
        description="Always-on research agent on the dream runtime.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="ohmo's working repo (briefs, queue, runtime state live here)",
    )
    parser.add_argument(
        "--wake-idle-minutes",
        type=int,
        default=30,
        help="fire a research heartbeat after this many idle minutes",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=16,
        help="turn budget per research session",
    )
    parser.add_argument("--agent-id", default="ohmo")
    return parser.parse_args(argv)


def resolve_credentials(
    env: Mapping[str, str],
) -> tuple[str, str, str] | list[str]:
    """``(model, api_key, base_url)`` or the missing canonical var names."""
    api_key = env.get("DREAM_API_KEY") or env.get("DREAM_SMOKE_API_KEY")
    model = env.get("DREAM_MODEL") or env.get("DREAM_SMOKE_MODEL")
    base_url = (
        env.get("DREAM_BASE_URL")
        or env.get("DREAM_SMOKE_BASE_URL")
        or "https://api.openai.com/v1"
    )
    missing = [
        name
        for name, value in (("DREAM_API_KEY", api_key), ("DREAM_MODEL", model))
        if not value
    ]
    if missing:
        return missing
    assert api_key is not None and model is not None
    return model, api_key, base_url


def bootstrap_workspace(workspace: Path) -> Path:
    """Lay down ohmo's durable conventions; idempotent. Returns the heartbeat path.

    - ``docs/research/briefs/`` + ``INDEX.md`` — the product.
    - ``.harness/sandbox.toml`` — the net-allowlist tier so arxiv_search runs.
    - ``.harness/ohmo-heartbeat.md`` — the persona's wake prompt.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    briefs = workspace / "docs" / "research" / "briefs"
    briefs.mkdir(parents=True, exist_ok=True)
    index = workspace / "docs" / "research" / "INDEX.md"
    if not index.exists():
        index.write_text(_SEED_INDEX, encoding="utf-8")
    harness_dir = workspace / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    sandbox = harness_dir / "sandbox.toml"
    if not sandbox.exists():
        sandbox.write_text(_SANDBOX_TOML, encoding="utf-8")
    heartbeat = harness_dir / "ohmo-heartbeat.md"
    if not heartbeat.exists():
        heartbeat.write_text(OHMO_HEARTBEAT_PROMPT, encoding="utf-8")
    return heartbeat


def make_wake_run_handler(
    harness: Harness,
    *,
    events_path: Path,
    max_turns: int,
):
    """Turn a heartbeat ``run`` decision into persona research sessions.

    Each decided task gets its own session (fresh context, the persona as
    system prompt); progress is mirrored onto the runtime event stream as
    ``ohmo.research.*`` events. A failing task never kills the wake
    handler — the next task still runs and the failure is an event.
    """
    sink = EventSink(events_path)

    async def handler(decision: HeartbeatDecision) -> None:
        for task in decision.tasks:
            sink.emit("ohmo.research.started", task=task)
            try:
                tool_calls = await _run_research_session(
                    harness, task=task, max_turns=max_turns
                )
            except Exception as exc:
                sink.emit("ohmo.research.failed", task=task, error=repr(exc))
                continue
            sink.emit("ohmo.research.finished", task=task, tool_calls=tool_calls)

    return handler


async def _run_research_session(
    harness: Harness, *, task: str, max_turns: int
) -> int:
    """Drive one persona session to completion; return its tool-call count."""
    session = await harness.start_session(
        SessionOptions(system_prompt=OHMO_PERSONA, max_turns=max_turns)
    )
    tool_calls = 0
    prompt = (
        f"Wake task: {task}\n\n"
        "Work this task now following your persona's research workflow. "
        "End with your one-paragraph handoff summary."
    )
    async for event in session.send(prompt):
        if isinstance(event, ToolUseResult):
            tool_calls += 1
        elif isinstance(event, Error):
            raise RuntimeError(f"session error: {event}")
        elif isinstance(event, TurnComplete):
            continue
    return tool_calls


async def run_ohmo(
    *,
    workspace: Path,
    env: Mapping[str, str] | None = None,
    wake_idle_minutes: int = 30,
    max_turns: int = 16,
    agent_id: str = "ohmo",
    stderr: TextIO | None = None,
    install_signal_handlers: bool = True,
    on_started=None,
) -> int:
    """Construct ohmo and run until stopped (the daemon body, testable)."""
    err = stderr if stderr is not None else sys.stderr
    resolved_env = env if env is not None else os.environ
    credentials = resolve_credentials(resolved_env)
    if isinstance(credentials, list):
        err.write("missing required env vars: " + ", ".join(credentials) + "\n")
        return EXIT_MISSING_ENV
    model, api_key, base_url = credentials

    heartbeat_path = bootstrap_workspace(workspace)
    registry = default_registry()
    for tool in research_tools():
        registry.register(tool, source=ToolSource.PER_REPO)

    harness = build_harness(
        model=model,
        api_key=api_key,
        base_url=base_url,
        working_dir=workspace,
        max_turns=max_turns,
        registry=registry,
        env=resolved_env,
    )
    paths = harness.config.paths
    assert paths is not None  # build_harness always resolves them
    events_path = paths.dream_dir / "runtime" / "events.jsonl"
    runtime = Runtime(
        harness,
        RuntimeConfig(
            agent_id=agent_id,
            wake_idle_minutes=wake_idle_minutes,
            wake_prompt_path=heartbeat_path,
        ),
        wake_run_handler=make_wake_run_handler(
            harness, events_path=events_path, max_turns=max_turns
        ),
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
    args = parse_args(argv)
    return asyncio.run(
        run_ohmo(
            workspace=args.workspace,
            wake_idle_minutes=args.wake_idle_minutes,
            max_turns=args.max_turns,
            agent_id=args.agent_id,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
