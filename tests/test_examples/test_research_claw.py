"""research_claw — mini AutoResearchClaw (idea → experimentally tested paper).

Covers the agent's own logic offline: experiment execution + metrics
parsing through dream's real SubprocessSandbox, artifact mechanics, the
deterministic stage orchestrator with its experiment oracle + repair
loop, and the CLI exit codes. The LLM stage sessions are injected as a
fake so the pipeline control flow tests without a model or a network.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

_EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))

from research_claw.agent import run_paper  # noqa: E402
from research_claw.pipeline import (  # noqa: E402
    STAGES,
    PaperResult,
    StageContext,
    run_pipeline,
)
from research_claw.tools import (  # noqa: E402
    RunExperimentTool,
    SaveArtifactTool,
    extract_metrics,
)

from dream.tools._context import ToolExecutionContext  # noqa: E402


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s-test")


# --- metrics parsing --------------------------------------------------------


def test_extract_metrics_finds_last_json_line() -> None:
    stdout = "setting up\nrunning trial 1\n" + json.dumps(
        {"converged": True, "iterations": 42}
    )
    metrics = extract_metrics(stdout)
    assert metrics == {"converged": True, "iterations": 42}


def test_extract_metrics_ignores_prose_and_non_object_json() -> None:
    assert extract_metrics("just text, no json") is None
    assert extract_metrics("[1, 2, 3]") is None  # array, not a metrics object
    assert extract_metrics("") is None


def test_extract_metrics_prefers_last_object() -> None:
    stdout = '{"step": 1}\nmid\n{"step": 2, "final": true}'
    assert extract_metrics(stdout) == {"step": 2, "final": True}


# --- run_experiment tool (real sandbox) -------------------------------------


@pytest.mark.asyncio
async def test_run_experiment_executes_and_parses_metrics(tmp_path: Path) -> None:
    script = tmp_path / "experiment.py"
    script.write_text(
        "import json\nprint('working...')\n"
        "print(json.dumps({'accuracy': 0.91, 'ok': True}))\n",
        encoding="utf-8",
    )
    result = await RunExperimentTool().execute(
        {"path": "experiment.py"}, _ctx(tmp_path)
    )
    assert not result.is_error
    assert result.metadata["returncode"] == 0
    assert result.metadata["metrics"] == {"accuracy": 0.91, "ok": True}


@pytest.mark.asyncio
async def test_run_experiment_reports_failure_with_contract(tmp_path: Path) -> None:
    script = tmp_path / "broken.py"
    script.write_text("raise ValueError('boom in experiment')\n", encoding="utf-8")
    result = await RunExperimentTool().execute({"path": "broken.py"}, _ctx(tmp_path))
    assert result.is_error
    assert result.metadata["returncode"] != 0
    assert "boom in experiment" in result.content
    assert "root_cause" in result.metadata
    assert "stop_condition" in result.metadata


@pytest.mark.asyncio
async def test_run_experiment_missing_file_rejected(tmp_path: Path) -> None:
    result = await RunExperimentTool().execute({"path": "nope.py"}, _ctx(tmp_path))
    assert result.is_error
    assert "does not exist" in result.content


@pytest.mark.asyncio
async def test_run_experiment_escape_rejected(tmp_path: Path) -> None:
    result = await RunExperimentTool().execute(
        {"path": "../escape.py"}, _ctx(tmp_path)
    )
    assert result.is_error
    assert "outside the workspace" in result.content


@pytest.mark.asyncio
async def test_run_experiment_green_no_metrics_warns(tmp_path: Path) -> None:
    script = tmp_path / "silent.py"
    script.write_text("print('done, but no json metrics')\n", encoding="utf-8")
    result = await RunExperimentTool().execute({"path": "silent.py"}, _ctx(tmp_path))
    assert not result.is_error
    assert result.metadata.get("warning")
    assert result.metadata["metrics"] is None


@pytest.mark.asyncio
async def test_save_artifact_writes_file(tmp_path: Path) -> None:
    result = await SaveArtifactTool().execute(
        {"name": "problem.md", "markdown": "# Problem\n\nWe study X." + "y" * 40},
        _ctx(tmp_path),
    )
    assert not result.is_error
    assert (tmp_path / "problem.md").read_text(encoding="utf-8").startswith("# Problem")


@pytest.mark.asyncio
async def test_save_artifact_rejects_escape(tmp_path: Path) -> None:
    result = await SaveArtifactTool().execute(
        {"name": "../evil.md", "markdown": "x" * 50}, _ctx(tmp_path)
    )
    assert result.is_error
    assert not (tmp_path.parent / "evil.md").exists()


# --- pipeline orchestration (fake sessions) ---------------------------------


def test_stage_sequence_is_the_six_phases() -> None:
    assert [s.name for s in STAGES] == [
        "scope",
        "related_work",
        "experiment",
        "analysis",
        "paper",
        "review",
    ]


class _FakeRunner:
    """Plays the LLM stages by writing each stage's expected artifact.

    The experiment stage writes a real runnable script so the orchestrator's
    authoritative oracle run exercises the real sandbox.
    """

    def __init__(self, *, experiment_body: str) -> None:
        self.experiment_body = experiment_body
        self.calls: list[str] = []

    async def __call__(self, stage_name: str, ctx: StageContext) -> None:
        self.calls.append(stage_name)
        ws = ctx.workspace
        if stage_name == "scope":
            (ws / "problem.md").write_text("# Problem\n\nHypothesis: X.\n", "utf-8")
        elif stage_name == "related_work":
            (ws / "related_work.md").write_text("# Related\n\n- [paper](u)\n", "utf-8")
        elif stage_name == "experiment":
            (ws / "experiment.py").write_text(self.experiment_body, "utf-8")
        elif stage_name == "analysis":
            (ws / "analysis.md").write_text(
                f"# Analysis\n\nResults: {ctx.results}\n", "utf-8"
            )
        elif stage_name == "paper":
            (ws / "paper.md").write_text(
                f"# Paper\n\nWe measured: {ctx.results}\n", "utf-8"
            )
        elif stage_name == "review":
            (ws / "review.md").write_text("# Review\n\nAccept.\n", "utf-8")


_GOOD_EXPERIMENT = (
    "import json\nprint('ran')\nprint(json.dumps({'metric': 0.5, 'ok': True}))\n"
)
_BROKEN_THEN_FIXED = "raise RuntimeError('nope')\n"


@pytest.mark.asyncio
async def test_pipeline_runs_experiment_and_writes_paper(tmp_path: Path) -> None:
    runner = _FakeRunner(experiment_body=_GOOD_EXPERIMENT)
    result = await run_pipeline(
        idea="Momentum helps",
        workspace=tmp_path,
        run_stage=runner,
    )
    assert isinstance(result, PaperResult)
    assert result.ok
    assert runner.calls == [s.name for s in STAGES]
    # The authoritative oracle run captured real metrics into results.json.
    results = json.loads((tmp_path / "results.json").read_text("utf-8"))
    assert results["metrics"] == {"metric": 0.5, "ok": True}
    assert results["returncode"] == 0
    # The paper exists and the analysis/paper stages saw the real results.
    paper = (tmp_path / "paper.md").read_text("utf-8")
    assert "metric" in paper
    assert result.paper_path == tmp_path / "paper.md"


@pytest.mark.asyncio
async def test_pipeline_oracle_marks_unverified_when_experiment_red(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner(experiment_body=_BROKEN_THEN_FIXED)
    result = await run_pipeline(
        idea="Broken idea",
        workspace=tmp_path,
        run_stage=runner,
    )
    # A red experiment does not abort the paper — but it is honestly marked
    # unverified (AutoResearchClaw's proceed/pivot; the paper still writes).
    assert not result.experiment_verified
    assert (tmp_path / "paper.md").exists()
    results = json.loads((tmp_path / "results.json").read_text("utf-8"))
    assert results["returncode"] != 0
    assert results["metrics"] is None


@pytest.mark.asyncio
async def test_pipeline_missing_experiment_is_unverified(tmp_path: Path) -> None:
    class _NoExperimentRunner(_FakeRunner):
        async def __call__(self, stage_name: str, ctx: StageContext) -> None:
            if stage_name == "experiment":
                self.calls.append(stage_name)
                return  # forget to write experiment.py
            await super().__call__(stage_name, ctx)

    result = await run_pipeline(
        idea="x", workspace=tmp_path, run_stage=_NoExperimentRunner(experiment_body="")
    )
    assert not result.experiment_verified
    assert (tmp_path / "paper.md").exists()


# --- CLI --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_paper_missing_env_exit_2(tmp_path: Path) -> None:
    stderr = io.StringIO()
    code = await run_paper(
        idea="anything", workspace=tmp_path / "ws", env={}, stderr=stderr
    )
    assert code == 2
    assert "DREAM_API_KEY" in stderr.getvalue()


@pytest.mark.asyncio
async def test_run_paper_requires_idea(tmp_path: Path) -> None:
    stderr = io.StringIO()
    code = await run_paper(
        idea="  ",
        workspace=tmp_path / "ws",
        env={"DREAM_API_KEY": "k", "DREAM_MODEL": "m"},
        stderr=stderr,
    )
    assert code == 2
    assert "idea" in stderr.getvalue().lower()
