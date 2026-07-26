"""research_claw — a cron-driven paper-factory agent on the dream runtime.

No orchestrator. The dream Runtime's cron loop fires every N hours and
spawns ``--once`` as a supervised background task. Each ``--once``:

1. pops the next idea from the ``ideas.md`` queue (you append ideas;
   the agent works them off),
2. hands it to ONE autonomous researcher session that owns its whole
   workflow (arxiv_search, write_file, run_experiment, save_artifact)
   and writes everything under ``papers/{stamp}-{slug}/``,
3. afterwards the harness re-runs ``experiment.py`` itself — the oracle
   — records ``results.json``, and stamps the verdict into
   ``papers/INDEX.md``.

Exit codes: 0 ok (including an empty queue — a quiet cron no-op),
1 session produced no paper, 2 bad input / missing credentials,
3 boot blocked, 4 already running.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import signal
import sys
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from dream import build_harness
from dream.events import Error
from dream.session import SessionOptions
from dream.tasks._cron import CronManifest, load_cron_jobs, save_cron_jobs
from dream.tools._registry import ToolSource
from dream.tools.builtin import default_registry

if __name__ == "__main__" and not __package__:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_claw.personas import RESEARCHER_PERSONA, paper_instruction
from research_claw.tools import (
    ArxivSearchTool,
    RunExperimentTool,
    SaveArtifactTool,
    run_experiment_file,
)


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

EXIT_OK = 0
EXIT_NO_PAPER = 1
EXIT_BAD_INPUT = 2
EXIT_BOOT_BLOCKED = 3
EXIT_ALREADY_RUNNING = 4

CRON_JOB_NAME = "paper-factory"
IDEAS_FILE = "ideas.md"
IDEAS_DONE_FILE = "ideas_done.md"
PAPERS_DIR = "papers"
SESSION_MAX_TURNS = 40

_SANDBOX_TOML = """\
# research_claw posture: repo-write+net-allowlist so arxiv_search (tier 2)
# runs; run_experiment / save_artifact / write_file are tier 1.
tier = "repo-write+net-allowlist"
"""

_TIER_OVERRIDES_TOML = """\
[arxiv_search]
tier_required = "repo-write+net-allowlist"
promoted_by = "research-claw-bootstrap"
promoted_at = "{today}"
reason = "pinned-host arXiv search for the related-work survey"

[run_experiment]
tier_required = "repo-write"
promoted_by = "research-claw-bootstrap"
promoted_at = "{today}"
reason = "runs the generated experiment script in the workspace sandbox"

[save_artifact]
tier_required = "repo-write"
promoted_by = "research-claw-bootstrap"
promoted_at = "{today}"
reason = "writes research artifacts (problem/paper/...) in the workspace"
"""

_CRON_MANIFEST_TOML = """\
name = "{name}"
enabled = true
schedule = "{schedule}"
description = "Pop one idea from ideas.md and produce a tested paper."
"""

_IDEAS_SEED = """\
# research_claw idea queue — one idea per line; lines starting with '#'
# are ignored. The paper factory pops the TOP idea each cron firing.
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="research_claw",
        description="Cron-driven paper factory: ideas.md in, tested papers out.",
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd() / "paper-lab")
    parser.add_argument(
        "--every-hours",
        type=int,
        default=6,
        choices=range(1, 25),
        metavar="1-24",
        help="cron cadence (default: every %(default)s hours, first fire now)",
    )
    parser.add_argument(
        "--idea",
        default=None,
        help="(--once only) work this idea instead of popping the queue",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="produce one paper now and exit (what cron spawns)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Idea queue
# ---------------------------------------------------------------------------


def pop_next_idea(workspace: Path) -> str | None:
    """Remove and return the first queued idea; None when the queue is empty."""
    queue_path = workspace / IDEAS_FILE
    if not queue_path.exists():
        return None
    lines = queue_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        idea = line.strip()
        if idea and not idea.startswith("#"):
            remaining = lines[:index] + lines[index + 1 :]
            queue_path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
            return idea.removeprefix("- ").strip()
    return None


def mark_idea_done(workspace: Path, *, idea: str, paper_dir: str, verdict: str) -> None:
    done = workspace / IDEAS_DONE_FILE
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with done.open("a", encoding="utf-8") as fh:
        fh.write(f"- [{stamp}] {verdict}: {idea} -> {paper_dir}/\n")


def paper_slug(idea: str) -> str:
    words = re.findall(r"[a-z0-9]+", idea.lower())[:5]
    return "-".join(words) or "paper"


def run_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H-%M")


# ---------------------------------------------------------------------------
# Workspace bootstrap + cron wiring
# ---------------------------------------------------------------------------


def bootstrap_workspace(workspace: Path, *, every_hours: int = 6) -> None:
    """Lay down posture, promotions, the cron manifest, and the idea queue."""
    (workspace / PAPERS_DIR).mkdir(parents=True, exist_ok=True)
    harness_dir = workspace / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    sandbox = harness_dir / "sandbox.toml"
    if not sandbox.exists():
        sandbox.write_text(_SANDBOX_TOML, encoding="utf-8")
    overrides = harness_dir / "tool-tier-overrides.toml"
    if not overrides.exists():
        today = datetime.now().strftime("%Y-%m-%d")
        overrides.write_text(_TIER_OVERRIDES_TOML.format(today=today), encoding="utf-8")
    manifest = harness_dir / "cron" / f"{CRON_JOB_NAME}.toml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if not manifest.exists():
        manifest.write_text(
            _CRON_MANIFEST_TOML.format(
                name=CRON_JOB_NAME, schedule=f"0 */{every_hours} * * *"
            ),
            encoding="utf-8",
        )
    ideas = workspace / IDEAS_FILE
    if not ideas.exists():
        ideas.write_text(_IDEAS_SEED, encoding="utf-8")


def make_cron_argv_builder(*, workspace: Path) -> Callable[[CronManifest], list[str]]:
    """Map the ``paper-factory`` manifest to this script's ``--once`` mode."""
    agent_path = Path(__file__).resolve()

    def argv_for(manifest: CronManifest) -> list[str]:
        if manifest.name == CRON_JOB_NAME:
            return [
                sys.executable,
                str(agent_path),
                "--once",
                "--workspace",
                str(workspace),
            ]
        return [sys.executable, "-c", f"print('cron:{manifest.name} fired')"]

    return argv_for


def fire_now(registry_path: Path) -> None:
    """Backdate the factory job so the first paper starts immediately."""
    jobs = load_cron_jobs(registry_path)
    save_cron_jobs(
        registry_path,
        [
            job.model_copy(update={"next_run": datetime.now(UTC)})
            if job.name == CRON_JOB_NAME
            else job
            for job in jobs
        ],
    )


# ---------------------------------------------------------------------------
# One paper (--once)
# ---------------------------------------------------------------------------

# Drives one researcher session for `instruction`. Injectable for tests.
SessionRunner = Callable[[str], Awaitable[None]]


def _make_real_session_runner(
    *, model: str, api_key: str, base_url: str, workspace: Path, env: Mapping[str, str]
) -> SessionRunner:
    registry = default_registry()
    for tool in (ArxivSearchTool(), RunExperimentTool(), SaveArtifactTool()):
        registry.register(tool, source=ToolSource.PER_REPO)
    harness = build_harness(
        model=model,
        api_key=api_key,
        base_url=base_url,
        working_dir=workspace,
        max_turns=SESSION_MAX_TURNS,
        registry=registry,
        env=env,
    )

    async def run_session(instruction: str) -> None:
        session = await harness.start_session(
            SessionOptions(
                system_prompt=RESEARCHER_PERSONA, max_turns=SESSION_MAX_TURNS
            )
        )
        async for event in session.send(instruction):
            if isinstance(event, Error):
                raise RuntimeError(f"researcher session error: {event}")

    return run_session


async def _oracle(workspace: Path, paper_dir: str) -> tuple[bool, str]:
    """Re-run the paper's experiment authoritatively; return (verified, verdict).

    Writes ``results.json`` next to the paper. Verified means: the script
    exists, ran green under the harness's own sandbox run, and printed a
    metrics JSON — the same bar the in-session run_experiment sets, but
    measured by the harness, not claimed by the model.
    """
    experiment_rel = f"{paper_dir}/experiment.py"
    paper_exists = (workspace / paper_dir / "paper.md").exists()
    if not (workspace / experiment_rel).is_file():
        results = {"ran": False, "returncode": None, "metrics": None,
                   "note": "no experiment.py was produced"}
        verified = False
    else:
        run = await run_experiment_file(workspace, experiment_rel)
        results = {
            "ran": True,
            "returncode": run.returncode,
            "timed_out": run.timed_out,
            "metrics": run.metrics,
            "stdout_tail": run.stdout_tail,
            "stderr_tail": run.stderr_tail,
        }
        verified = run.green and run.metrics is not None
    (workspace / paper_dir / "results.json").parent.mkdir(parents=True, exist_ok=True)
    (workspace / paper_dir / "results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    if not paper_exists:
        return False, "NO-PAPER"
    return verified, "VERIFIED" if verified else "UNVERIFIED"


def _index_add(workspace: Path, *, idea: str, paper_dir: str, verdict: str) -> None:
    index = workspace / PAPERS_DIR / "INDEX.md"
    if not index.exists():
        index.write_text("# Papers\n\n", encoding="utf-8")
    with index.open("a", encoding="utf-8") as fh:
        fh.write(f"- {verdict} — [{idea}]({paper_dir.removeprefix(PAPERS_DIR + '/')}/paper.md)\n")


async def run_once(
    *,
    workspace: Path,
    env: Mapping[str, str] | None = None,
    idea: str | None = None,
    stderr: TextIO | None = None,
    stdout: TextIO | None = None,
    run_session: SessionRunner | None = None,
    stamp: str | None = None,
) -> int:
    """Pop one idea (or take ``idea``), run one researcher session, verify."""
    err = stderr if stderr is not None else sys.stderr
    out = stdout if stdout is not None else sys.stdout
    resolved_env = dict(env if env is not None else os.environ)
    credentials = resolve_credentials(resolved_env)
    if isinstance(credentials, list):
        err.write("missing required env vars: " + ", ".join(credentials) + "\n")
        return EXIT_BAD_INPUT
    model, api_key, base_url = credentials

    bootstrap_workspace(workspace)
    chosen = idea.strip() if idea and idea.strip() else pop_next_idea(workspace)
    if not chosen:
        out.write("idea queue is empty — nothing to do this cycle\n")
        return EXIT_OK

    paper_dir = f"{PAPERS_DIR}/{stamp or run_stamp()}-{paper_slug(chosen)}"
    (workspace / paper_dir).mkdir(parents=True, exist_ok=True)
    runner = run_session or _make_real_session_runner(
        model=model,
        api_key=api_key,
        base_url=base_url,
        workspace=workspace,
        env=resolved_env,
    )
    out.write(f"working idea: {chosen}\npaper dir:    {paper_dir}/\n")
    out.flush()
    try:
        await runner(paper_instruction(idea=chosen, paper_dir=paper_dir))
    except Exception as exc:
        err.write(f"researcher session failed: {exc}\n")
        # Fall through: the oracle still records what (if anything) was left.

    _verified, verdict = await _oracle(workspace, paper_dir)
    _index_add(workspace, idea=chosen, paper_dir=paper_dir, verdict=verdict)
    mark_idea_done(workspace, idea=chosen, paper_dir=paper_dir, verdict=verdict)
    out.write(f"verdict:      {verdict}\n")
    out.flush()
    return EXIT_OK if verdict != "NO-PAPER" else EXIT_NO_PAPER


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.once:
        return asyncio.run(run_once(workspace=args.workspace, idea=args.idea))
    if args.idea:
        sys.stderr.write("--idea only applies with --once\n")
        return EXIT_BAD_INPUT
    # ponytail: daemon mode went with the dream Runtime — run via cron/--once
    sys.stderr.write("daemon mode was removed; use --once (e.g. from cron)\n")
    return EXIT_BAD_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
