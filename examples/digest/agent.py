"""digest — a rolling self-evolution AI digest, delivered by the dream runtime.

Where ohmo is wake-driven, digest is clock-driven: a dream cron manifest
fires every 2 hours, the runtime spawns this script's ``--once`` mode as
a supervised background task, and each run drops a timestamped markdown
file under ``research_ideas/`` covering the last 2 hours. No email — the
repo is the system of record.

Modes:

- **daemon** (default): cron fires the ``rolling-digest`` manifest every
  2 hours; the first run is backdated to *now* so it starts immediately.
- **--once**: build a harness, run one digest session (HN + arXiv over
  the window → ``research_ideas/{stamp}.md``), verify the file, exit.

Exit codes mirror ``dream.daemon``: 0 ok, 1 run failed, 2 missing
credentials, 3 boot blocked, 4 already running.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from dream import (
    Runtime,
    RuntimeBootBlockedError,
    RuntimeBusyError,
    RuntimeConfig,
    build_harness,
)
from dream.events import Error
from dream.session import SessionOptions
from dream.tasks._cron import CronManifest, load_cron_jobs, save_cron_jobs
from dream.tools._registry import ToolRegistry, ToolSource
from dream.tools.builtin import default_registry

if __name__ == "__main__" and not __package__:  # pragma: no cover
    # Allow ``python examples/digest/agent.py`` without installing anything.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ohmo.agent import resolve_credentials
from ohmo.tools import ArxivSearchTool

from digest.persona import DEFAULT_TOPIC, DIGEST_PERSONA, digest_instruction
from digest.tools import (
    RESEARCH_IDEAS_DIR,
    HnSearchTool,
    SaveDigestTool,
)

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_MISSING_ENV = 2
EXIT_BOOT_BLOCKED = 3
EXIT_ALREADY_RUNNING = 4

WINDOW_HOURS = 2
CRON_JOB_NAME = "rolling-digest"
# Every 2 hours, on the hour; the first fire is backdated to now at boot.
CRON_SCHEDULE = "0 */2 * * *"

_SANDBOX_TOML = """\
# Digest posture: net-allowlist tier so arxiv_search / hn_search (tier 2)
# are callable. Edit freely.
tier = "repo-write+net-allowlist"
"""

# Spec 13B trust ramp: per-repo tools start read-only; bootstrap (the
# workspace operator) promotes the digest's tools.
_TIER_OVERRIDES_TOML = """\
[arxiv_search]
tier_required = "repo-write+net-allowlist"
promoted_by = "digest-bootstrap"
promoted_at = "{today}"
reason = "pinned-host arXiv search; query terms only"

[hn_search]
tier_required = "repo-write+net-allowlist"
promoted_by = "digest-bootstrap"
promoted_at = "{today}"
reason = "pinned-host Hacker News search; query terms only"

[save_digest]
tier_required = "repo-write"
promoted_by = "digest-bootstrap"
promoted_at = "{today}"
reason = "writes the digest under research_ideas/ inside the workspace"
"""

_CRON_MANIFEST_TOML = """\
name = "{name}"
enabled = true
schedule = "{schedule}"
description = "Rolling self-evolution AI digest (spawns agent.py --once)."
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="digest",
        description="Rolling AI-news digest agent on the dream runtime.",
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument(
        "--topic",
        default=DEFAULT_TOPIC,
        help="what the digest covers (default: self-evolving AI)",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=WINDOW_HOURS,
        help="how many hours back each run covers (default: %(default)s)",
    )
    parser.add_argument("--max-turns", type=int, default=14)
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one digest now and exit (what cron spawns)",
    )
    return parser.parse_args(argv)


def run_stamp() -> str:
    """Filesystem-safe minute-resolution stamp, e.g. ``2026-06-10T14-30``."""
    return datetime.now().strftime("%Y-%m-%dT%H-%M")


def bootstrap_workspace(workspace: Path) -> None:
    """Lay down the digest's conventions; idempotent."""
    (workspace / RESEARCH_IDEAS_DIR).mkdir(parents=True, exist_ok=True)
    harness_dir = workspace / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    sandbox = harness_dir / "sandbox.toml"
    if not sandbox.exists():
        sandbox.write_text(_SANDBOX_TOML, encoding="utf-8")
    overrides = harness_dir / "tool-tier-overrides.toml"
    if not overrides.exists():
        today = datetime.now().strftime("%Y-%m-%d")
        overrides.write_text(
            _TIER_OVERRIDES_TOML.format(today=today), encoding="utf-8"
        )
    manifest = harness_dir / "cron" / f"{CRON_JOB_NAME}.toml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if not manifest.exists():
        manifest.write_text(
            _CRON_MANIFEST_TOML.format(name=CRON_JOB_NAME, schedule=CRON_SCHEDULE),
            encoding="utf-8",
        )


def make_cron_argv_builder(
    *, workspace: Path, topic: str, window_hours: int
) -> Callable[[CronManifest], list[str]]:
    """Map the ``rolling-digest`` manifest to this script's ``--once`` mode."""
    agent_path = Path(__file__).resolve()

    def argv_for(manifest: CronManifest) -> list[str]:
        if manifest.name == CRON_JOB_NAME:
            return [
                sys.executable,
                str(agent_path),
                "--once",
                "--workspace",
                str(workspace),
                "--topic",
                topic,
                "--window-hours",
                str(window_hours),
            ]
        return [sys.executable, "-c", f"print('cron:{manifest.name} fired')"]

    return argv_for


def fire_now(registry_path: Path) -> None:
    """Backdate the digest job's next_run so the first run fires immediately.

    The runtime seeds next_run from the schedule (the next even hour); for
    "starting from now" we pull it back to the present so the cron tick
    fires it on the next poll, then the schedule takes over.
    """
    jobs = load_cron_jobs(registry_path)
    backdated = [
        job.model_copy(update={"next_run": datetime.now(UTC)})
        if job.name == CRON_JOB_NAME
        else job
        for job in jobs
    ]
    save_cron_jobs(registry_path, backdated)


def _build_registry(*, stamp: str) -> ToolRegistry:
    registry = default_registry()
    for tool in (ArxivSearchTool(), HnSearchTool(), SaveDigestTool(stamp=stamp)):
        registry.register(tool, source=ToolSource.PER_REPO)
    return registry


async def run_digest_once(
    *,
    workspace: Path,
    env: Mapping[str, str] | None = None,
    topic: str = DEFAULT_TOPIC,
    window_hours: int = WINDOW_HOURS,
    max_turns: int = 14,
    stderr: TextIO | None = None,
    stamp: str | None = None,
) -> int:
    """One digest session: search the window → compose → save → verify."""
    err = stderr if stderr is not None else sys.stderr
    resolved_env = dict(env if env is not None else os.environ)
    credentials = resolve_credentials(resolved_env)
    if isinstance(credentials, list):
        err.write("missing required env vars: " + ", ".join(credentials) + "\n")
        return EXIT_MISSING_ENV
    model, api_key, base_url = credentials

    bootstrap_workspace(workspace)
    run_id = stamp or run_stamp()
    harness = build_harness(
        model=model,
        api_key=api_key,
        base_url=base_url,
        working_dir=workspace,
        max_turns=max_turns,
        registry=_build_registry(stamp=run_id),
        env=resolved_env,
    )
    session = await harness.start_session(
        SessionOptions(system_prompt=DIGEST_PERSONA, max_turns=max_turns)
    )
    async for event in session.send(
        digest_instruction(topic=topic, window_hours=window_hours, stamp=run_id)
    ):
        if isinstance(event, Error):
            err.write(f"digest session error: {event}\n")
            return EXIT_RUN_FAILED
    # Deterministic exit check (the oracle habit): the deliverable is the
    # timestamped file the save tool writes — no file, no digest.
    out = workspace / RESEARCH_IDEAS_DIR / f"{run_id}.md"
    if not out.exists():
        err.write("digest session ended without producing the digest file\n")
        return EXIT_RUN_FAILED
    return EXIT_OK


async def run_digest_daemon(
    *,
    workspace: Path,
    env: Mapping[str, str] | None = None,
    topic: str = DEFAULT_TOPIC,
    window_hours: int = WINDOW_HOURS,
    max_turns: int = 14,
    stderr: TextIO | None = None,
    install_signal_handlers: bool = True,
    on_started=None,
) -> int:
    """The long-running mode: cron fires the digest every 2 hours, from now."""
    err = stderr if stderr is not None else sys.stderr
    resolved_env = dict(env if env is not None else os.environ)
    credentials = resolve_credentials(resolved_env)
    if isinstance(credentials, list):
        err.write("missing required env vars: " + ", ".join(credentials) + "\n")
        return EXIT_MISSING_ENV
    model, api_key, base_url = credentials

    bootstrap_workspace(workspace)
    harness = build_harness(
        model=model,
        api_key=api_key,
        base_url=base_url,
        working_dir=workspace,
        max_turns=max_turns,
        env=resolved_env,
    )
    # build_harness seeded the cron registry; pull the first run to now.
    registry_path = harness.config.cron_registry_path
    if registry_path is not None:
        fire_now(registry_path)
    runtime = Runtime(
        harness,
        RuntimeConfig(agent_id="digest"),
        cron_argv_builder=make_cron_argv_builder(
            workspace=workspace, topic=topic, window_hours=window_hours
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
    if args.once:
        return asyncio.run(
            run_digest_once(
                workspace=args.workspace,
                topic=args.topic,
                window_hours=args.window_hours,
                max_turns=args.max_turns,
            )
        )
    return asyncio.run(
        run_digest_daemon(
            workspace=args.workspace,
            topic=args.topic,
            window_hours=args.window_hours,
            max_turns=args.max_turns,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
