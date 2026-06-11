"""research_claw — the cron-driven paper-factory agent.

Covers the agent's own logic offline: the experiment tools against the
real SubprocessSandbox, the idea queue, workspace bootstrap (cron
manifest + promotions), the cron argv payload, and ``run_once`` with an
injected session runner (verified / unverified / no-paper / empty-queue
paths). The runtime cron loop itself is covered by tests/test_runtime.
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

from research_claw.agent import (  # noqa: E402
    CRON_JOB_NAME,
    bootstrap_workspace,
    make_cron_argv_builder,
    paper_slug,
    parse_args,
    pop_next_idea,
    run_once,
)
from research_claw.personas import RESEARCHER_PERSONA, paper_instruction  # noqa: E402
from research_claw.tools import (  # noqa: E402
    RunExperimentTool,
    SaveArtifactTool,
    extract_metrics,
)

from dream.tasks._cron import CronManifest, load_cron_manifest  # noqa: E402
from dream.tools._context import ToolExecutionContext  # noqa: E402

_ENV = {"DREAM_API_KEY": "k", "DREAM_MODEL": "m"}


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s-test")


# --- metrics parsing --------------------------------------------------------


def test_extract_metrics_finds_last_json_object() -> None:
    stdout = '{"step": 1}\nprose\n{"step": 2, "final": true}'
    assert extract_metrics(stdout) == {"step": 2, "final": True}
    assert extract_metrics("no json here") is None
    assert extract_metrics("[1, 2]") is None


# --- run_experiment tool (real sandbox) -------------------------------------


@pytest.mark.asyncio
async def test_run_experiment_executes_and_parses_metrics(tmp_path: Path) -> None:
    (tmp_path / "experiment.py").write_text(
        "import json\nprint('working')\nprint(json.dumps({'acc': 0.9}))\n",
        encoding="utf-8",
    )
    result = await RunExperimentTool().execute({"path": "experiment.py"}, _ctx(tmp_path))
    assert not result.is_error
    assert result.metadata["metrics"] == {"acc": 0.9}


@pytest.mark.asyncio
async def test_run_experiment_failure_carries_contract(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("raise ValueError('boom')\n", encoding="utf-8")
    result = await RunExperimentTool().execute({"path": "broken.py"}, _ctx(tmp_path))
    assert result.is_error
    assert "boom" in result.content
    assert "stop_condition" in result.metadata


@pytest.mark.asyncio
async def test_run_experiment_rejects_escape_and_missing(tmp_path: Path) -> None:
    escape = await RunExperimentTool().execute({"path": "../e.py"}, _ctx(tmp_path))
    assert escape.is_error and "outside the workspace" in escape.content
    missing = await RunExperimentTool().execute({"path": "nope.py"}, _ctx(tmp_path))
    assert missing.is_error and "does not exist" in missing.content


@pytest.mark.asyncio
async def test_save_artifact_nested_path_and_escape(tmp_path: Path) -> None:
    ok = await SaveArtifactTool().execute(
        {"name": "papers/x/paper.md", "markdown": "# P\n\nbody " + "y" * 20},
        _ctx(tmp_path),
    )
    assert not ok.is_error
    assert (tmp_path / "papers" / "x" / "paper.md").exists()
    bad = await SaveArtifactTool().execute(
        {"name": "../evil.md", "markdown": "x" * 30}, _ctx(tmp_path)
    )
    assert bad.is_error


# --- idea queue ---------------------------------------------------------------


def test_pop_next_idea_skips_comments_and_pops_in_order(tmp_path: Path) -> None:
    (tmp_path / "ideas.md").write_text(
        "# queue\n\n- idea one\nidea two\n", encoding="utf-8"
    )
    assert pop_next_idea(tmp_path) == "idea one"
    assert pop_next_idea(tmp_path) == "idea two"
    assert pop_next_idea(tmp_path) is None
    # The comment header survives the pops.
    assert "# queue" in (tmp_path / "ideas.md").read_text(encoding="utf-8")


def test_pop_next_idea_missing_file(tmp_path: Path) -> None:
    assert pop_next_idea(tmp_path) is None


def test_paper_slug() -> None:
    assert paper_slug("Nesterov Momentum beats GD, always!") == (
        "nesterov-momentum-beats-gd-always"
    )
    assert paper_slug("???") == "paper"


# --- bootstrap + cron ----------------------------------------------------------


def test_bootstrap_writes_manifest_promotions_and_queue(tmp_path: Path) -> None:
    bootstrap_workspace(tmp_path, every_hours=4)
    manifest = load_cron_manifest(
        tmp_path / ".harness" / "cron" / f"{CRON_JOB_NAME}.toml"
    )
    assert manifest.schedule == "0 */4 * * *"
    overrides = (tmp_path / ".harness" / "tool-tier-overrides.toml").read_text(
        encoding="utf-8"
    )
    for name in ("arxiv_search", "run_experiment", "save_artifact"):
        assert f"[{name}]" in overrides
    assert (tmp_path / "ideas.md").exists()
    assert (tmp_path / "papers").is_dir()


def test_cron_argv_builder_targets_once(tmp_path: Path) -> None:
    argv_for = make_cron_argv_builder(workspace=tmp_path)
    argv = argv_for(CronManifest(name=CRON_JOB_NAME, schedule="0 */6 * * *"))
    assert "--once" in argv and str(tmp_path) in argv
    assert argv[1].endswith("agent.py")
    other = argv_for(CronManifest(name="other", schedule="0 1 * * *"))
    assert "--once" not in other


def test_parse_args_defaults() -> None:
    args = parse_args([])
    assert args.every_hours == 6
    assert not args.once
    assert args.idea is None


def test_persona_and_instruction() -> None:
    assert "run_experiment" in RESEARCHER_PERSONA
    text = paper_instruction(idea="momentum helps", paper_dir="papers/x")
    assert "papers/x/" in text
    assert "momentum helps" in text


# --- run_once (injected session) -----------------------------------------------


def _session_writing(workspace: Path, *, experiment: str | None, paper: bool):
    async def run_session(instruction: str) -> None:
        # The paper dir is named in the instruction; extract it like the agent
        # would read it.
        paper_dir = instruction.split("Your paper directory: ")[1].split("/\n")[0]
        target = workspace / paper_dir
        target.mkdir(parents=True, exist_ok=True)
        if experiment is not None:
            (target / "experiment.py").write_text(experiment, encoding="utf-8")
        if paper:
            (target / "paper.md").write_text("# Paper\n\nMeasured.\n", encoding="utf-8")

    return run_session


_GREEN = "import json\nprint(json.dumps({'m': 1, 'hypothesis_supported': True}))\n"


@pytest.mark.asyncio
async def test_run_once_verified_paper(tmp_path: Path) -> None:
    (tmp_path / "ideas.md").write_text("momentum helps convergence\n", encoding="utf-8")
    out = io.StringIO()
    code = await run_once(
        workspace=tmp_path,
        env=_ENV,
        stdout=out,
        stderr=out,
        run_session=_session_writing(tmp_path, experiment=_GREEN, paper=True),
        stamp="2026-06-10T18-00",
    )
    assert code == 0
    assert "VERIFIED" in out.getvalue()
    paper_dir = tmp_path / "papers" / "2026-06-10T18-00-momentum-helps-convergence"
    results = json.loads((paper_dir / "results.json").read_text(encoding="utf-8"))
    assert results["metrics"] == {"m": 1, "hypothesis_supported": True}
    index = (tmp_path / "papers" / "INDEX.md").read_text(encoding="utf-8")
    assert "VERIFIED" in index and "momentum helps convergence" in index
    # The idea left the queue and is logged as done.
    assert pop_next_idea(tmp_path) is None
    assert "momentum" in (tmp_path / "ideas_done.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_run_once_unverified_when_experiment_red(tmp_path: Path) -> None:
    out = io.StringIO()
    code = await run_once(
        workspace=tmp_path,
        env=_ENV,
        idea="broken idea",
        stdout=out,
        stderr=out,
        run_session=_session_writing(
            tmp_path, experiment="raise RuntimeError('x')\n", paper=True
        ),
        stamp="2026-06-10T18-01",
    )
    assert code == 0  # paper exists; honestly marked
    assert "UNVERIFIED" in out.getvalue()


@pytest.mark.asyncio
async def test_run_once_no_paper_exit_1(tmp_path: Path) -> None:
    out = io.StringIO()
    code = await run_once(
        workspace=tmp_path,
        env=_ENV,
        idea="lazy session",
        stdout=out,
        stderr=out,
        run_session=_session_writing(tmp_path, experiment=None, paper=False),
        stamp="2026-06-10T18-02",
    )
    assert code == 1
    assert "NO-PAPER" in out.getvalue()


@pytest.mark.asyncio
async def test_run_once_crashing_session_still_audited(tmp_path: Path) -> None:
    async def crashing(instruction: str) -> None:
        raise RuntimeError("model exploded")

    out = io.StringIO()
    err = io.StringIO()
    code = await run_once(
        workspace=tmp_path,
        env=_ENV,
        idea="doomed idea",
        stdout=out,
        stderr=err,
        run_session=crashing,
        stamp="2026-06-10T18-03",
    )
    assert code == 1
    assert "session failed" in err.getvalue()
    assert "NO-PAPER" in out.getvalue()


@pytest.mark.asyncio
async def test_run_once_empty_queue_is_quiet_noop(tmp_path: Path) -> None:
    out = io.StringIO()
    code = await run_once(workspace=tmp_path, env=_ENV, stdout=out, stderr=out)
    assert code == 0
    assert "queue is empty" in out.getvalue()
    assert not list((tmp_path / "papers").glob("*/"))


@pytest.mark.asyncio
async def test_run_once_missing_env_exit_2(tmp_path: Path) -> None:
    err = io.StringIO()
    code = await run_once(workspace=tmp_path, env={}, stderr=err, stdout=err)
    assert code == 2
    assert "DREAM_API_KEY" in err.getvalue()
