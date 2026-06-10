"""The deterministic staged orchestrator (idea → tested paper).

Six stages, run in order, each a single focused session that writes its
artifact to the workspace. Between the ``experiment`` and ``analysis``
stages the orchestrator runs the generated script **itself** — the
authoritative oracle run, captured to ``results.json`` — so the analysis
and paper stages reason over real measured numbers, not the model's
claim that the code ran (AutoResearchClaw's sandbox-execution discipline
and dream's spec-15 oracle, same idea).

Deterministic control flow lives here; the LLM lives only inside each
stage's session. ``run_stage`` is injectable so the orchestration tests
without a model.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research_claw.tools import run_experiment_file

__all__ = [
    "STAGES",
    "PaperResult",
    "Stage",
    "StageContext",
    "StageRunner",
    "run_pipeline",
]

EXPERIMENT_FILE = "experiment.py"
RESULTS_FILE = "results.json"
PAPER_FILE = "paper.md"


@dataclass(frozen=True)
class Stage:
    """One pipeline stage: a name and the artifact it must leave behind."""

    name: str
    artifact: str
    description: str


# The mini pipeline — AutoResearchClaw's 8 phases collapsed to 6 stages.
STAGES: tuple[Stage, ...] = (
    Stage("scope", "problem.md", "frame the idea as a problem + testable hypothesis"),
    Stage("related_work", "related_work.md", "survey prior art via arxiv_search"),
    Stage("experiment", EXPERIMENT_FILE, "write runnable code that tests the hypothesis"),
    Stage("analysis", "analysis.md", "interpret the real experiment results"),
    Stage("paper", PAPER_FILE, "write the full paper citing the measured results"),
    Stage("review", "review.md", "review the paper for honesty + completeness"),
)


@dataclass
class StageContext:
    """What a stage session is handed: the workspace and prior outputs."""

    idea: str
    workspace: Path
    stage: Stage
    # Populated after the oracle run, so analysis/paper/review see real data.
    results: dict[str, Any] | None = None
    experiment_verified: bool = False


# A stage runner drives one stage's session to completion (writing its
# artifact). The agent supplies the real LLM-backed one; tests inject a fake.
StageRunner = Callable[[str, StageContext], Awaitable[None]]


@dataclass(frozen=True)
class PaperResult:
    """The pipeline's outcome."""

    paper_path: Path | None
    results_path: Path | None
    experiment_verified: bool
    stages_run: tuple[str, ...] = field(default_factory=tuple)
    missing_artifacts: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """A paper was produced (verified or honestly-unverified)."""
        return self.paper_path is not None and self.paper_path.exists()


async def run_pipeline(
    *,
    idea: str,
    workspace: Path,
    run_stage: StageRunner,
    experiment_timeout_seconds: float = 120.0,
) -> PaperResult:
    """Run all six stages, with the experiment oracle between 3 and 4."""
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "idea.txt").write_text(idea, encoding="utf-8")

    ctx = StageContext(idea=idea, workspace=workspace, stage=STAGES[0])
    stages_run: list[str] = []
    missing: list[str] = []

    for stage in STAGES:
        ctx.stage = stage
        await run_stage(stage.name, ctx)
        stages_run.append(stage.name)
        if not (workspace / stage.artifact).exists():
            missing.append(stage.artifact)

        # The oracle: right after the experiment stage, run the script
        # ourselves and stamp results.json. Everything downstream reads it.
        if stage.name == "experiment":
            ctx.results, ctx.experiment_verified = await _run_oracle(
                workspace, timeout_seconds=experiment_timeout_seconds
            )

    paper_path = workspace / PAPER_FILE
    return PaperResult(
        paper_path=paper_path if paper_path.exists() else None,
        results_path=(workspace / RESULTS_FILE)
        if (workspace / RESULTS_FILE).exists()
        else None,
        experiment_verified=ctx.experiment_verified,
        stages_run=tuple(stages_run),
        missing_artifacts=tuple(missing),
    )


async def _run_oracle(
    workspace: Path, *, timeout_seconds: float
) -> tuple[dict[str, Any], bool]:
    """Authoritative experiment run → ``results.json``; return (results, verified).

    ``verified`` is True only when the script exists, ran green, and printed
    a JSON metrics object. A red or missing experiment is recorded honestly
    (returncode/metrics in results.json) and the paper is marked unverified —
    it still writes, but it must not claim a result it does not have.
    """
    script = workspace / EXPERIMENT_FILE
    if not script.is_file():
        results = {
            "ran": False,
            "returncode": None,
            "metrics": None,
            "note": "no experiment.py was produced",
        }
        _write_results(workspace, results)
        return results, False

    run = await run_experiment_file(
        workspace, EXPERIMENT_FILE, timeout_seconds=timeout_seconds
    )
    results = {
        "ran": True,
        "returncode": run.returncode,
        "timed_out": run.timed_out,
        "metrics": run.metrics,
        "stdout_tail": run.stdout_tail,
        "stderr_tail": run.stderr_tail,
    }
    _write_results(workspace, results)
    verified = run.green and run.metrics is not None
    return results, verified


def _write_results(workspace: Path, results: dict[str, Any]) -> None:
    (workspace / RESULTS_FILE).write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
