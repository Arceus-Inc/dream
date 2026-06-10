"""research_claw — idea → experimentally tested paper, on the dream runtime.

Builds one harness (researcher persona, the builtin tools + arxiv_search +
run_experiment + save_artifact) and drives the six-stage pipeline. Each
stage is its own session so context stays fresh; the orchestrator runs the
experiment authoritatively between stages 3 and 4.

This is a one-shot construct (not a daemon): ``run_paper(idea)`` returns
when ``paper.md`` is written. Exit codes: 0 ok, 1 no paper produced,
2 bad input / missing credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from dream import build_harness
from dream.events import Error, ToolUseResult
from dream.harness import Harness
from dream.session import SessionOptions
from dream.tools._registry import ToolSource
from dream.tools.builtin import default_registry

if __name__ == "__main__" and not __package__:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ohmo.agent import resolve_credentials
from ohmo.tools import ArxivSearchTool

from research_claw.personas import RESEARCHER_PERSONA, stage_instruction
from research_claw.pipeline import PaperResult, StageContext, run_pipeline
from research_claw.tools import RunExperimentTool, SaveArtifactTool

EXIT_OK = 0
EXIT_NO_PAPER = 1
EXIT_BAD_INPUT = 2

_STAGE_MAX_TURNS = {
    "scope": 6,
    "related_work": 8,
    "experiment": 20,  # the iterate-until-green loop needs room
    "analysis": 8,
    "paper": 10,
    "review": 8,
}

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
reason = "pinned-host arXiv search for the related-work stage"

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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="research_claw",
        description="Turn a research idea into an experimentally tested paper.",
    )
    parser.add_argument("--idea", required=True, help="the research idea, in a sentence")
    parser.add_argument("--workspace", type=Path, default=Path.cwd() / "paper-lab")
    return parser.parse_args(argv)


def bootstrap_workspace(workspace: Path) -> None:
    """Lay down the sandbox posture + tool promotions; idempotent."""
    from datetime import datetime

    workspace.mkdir(parents=True, exist_ok=True)
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


def _build_harness(
    *, model: str, api_key: str, base_url: str, workspace: Path, env: Mapping[str, str]
) -> Harness:
    registry = default_registry()
    for tool in (ArxivSearchTool(), RunExperimentTool(), SaveArtifactTool()):
        registry.register(tool, source=ToolSource.PER_REPO)
    return build_harness(
        model=model,
        api_key=api_key,
        base_url=base_url,
        working_dir=workspace,
        registry=registry,
        env=env,
    )


def make_stage_runner(
    harness: Harness, *, on_event=None
):
    """A real LLM-backed stage runner: one persona session per stage."""

    async def run_stage(stage_name: str, ctx: StageContext) -> None:
        session = await harness.start_session(
            SessionOptions(
                system_prompt=RESEARCHER_PERSONA,
                max_turns=_STAGE_MAX_TURNS.get(stage_name, 10),
            )
        )
        prompt = stage_instruction(
            stage_name,
            idea=ctx.idea,
            results=ctx.results,
            experiment_verified=ctx.experiment_verified,
        )
        async for event in session.send(prompt):
            if isinstance(event, ToolUseResult) and on_event is not None:
                on_event(stage_name, event)
            elif isinstance(event, Error):
                raise RuntimeError(f"stage {stage_name} error: {event}")

    return run_stage


async def run_paper(
    *,
    idea: str,
    workspace: Path,
    env: Mapping[str, str] | None = None,
    stderr: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Drive the full pipeline for ``idea``; return an exit code."""
    err = stderr if stderr is not None else sys.stderr
    out = stdout if stdout is not None else sys.stdout
    if not idea or not idea.strip():
        err.write("a non-empty --idea is required\n")
        return EXIT_BAD_INPUT
    resolved_env = dict(env if env is not None else os.environ)
    credentials = resolve_credentials(resolved_env)
    if isinstance(credentials, list):
        err.write("missing required env vars: " + ", ".join(credentials) + "\n")
        return EXIT_BAD_INPUT
    model, api_key, base_url = credentials

    bootstrap_workspace(workspace)
    harness = _build_harness(
        model=model,
        api_key=api_key,
        base_url=base_url,
        workspace=workspace,
        env=resolved_env,
    )

    def _log(stage: str, event: ToolUseResult) -> None:
        out.write(f"[{stage}] tool result\n")
        out.flush()

    result: PaperResult = await run_pipeline(
        idea=idea,
        workspace=workspace,
        run_stage=make_stage_runner(harness, on_event=_log),
    )
    _report(result, workspace, out=out)
    return EXIT_OK if result.ok else EXIT_NO_PAPER


def _report(result: PaperResult, workspace: Path, *, out: TextIO) -> None:
    out.write("\n=== research_claw complete ===\n")
    out.write(f"stages run:   {', '.join(result.stages_run)}\n")
    out.write(
        "experiment:   "
        + ("VERIFIED (ran green with metrics)\n" if result.experiment_verified
           else "UNVERIFIED (see results.json / Limitations)\n")
    )
    if result.missing_artifacts:
        out.write(f"missing:      {', '.join(result.missing_artifacts)}\n")
    if result.paper_path is not None:
        out.write(f"paper:        {result.paper_path}\n")
    else:
        out.write("paper:        NOT produced\n")
    out.flush()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(run_paper(idea=args.idea, workspace=args.workspace))


if __name__ == "__main__":
    raise SystemExit(main())
