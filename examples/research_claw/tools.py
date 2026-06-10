"""research_claw tools — the experiment oracle and artifact writer.

``run_experiment`` is the load-bearing one: it executes a generated
Python script in dream's :class:`~dream.sandbox.SubprocessSandbox` (the
real thing, tree-killed on timeout) and parses a JSON metrics line from
its stdout. The model uses it to iterate code until it runs green; the
orchestrator then runs it once more, authoritatively, as the oracle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.sandbox import SubprocessSandbox
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

__all__ = [
    "ExperimentRun",
    "RunExperimentTool",
    "SaveArtifactTool",
    "extract_metrics",
    "run_experiment_file",
]

_OUTPUT_TAIL = 4000
_DEFAULT_TIMEOUT = 120.0


def extract_metrics(stdout: str) -> dict[str, Any] | None:
    """Return the last line of stdout that parses as a JSON object, else None.

    The convention the experiment persona is given: print a single JSON
    object as the final line with the run's metrics. Scanning bottom-up
    means setup chatter above it is ignored.
    """
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


class ExperimentRun(BaseModel):
    """Structured outcome of one authoritative experiment run."""

    path: str
    returncode: int | None
    timed_out: bool
    metrics: dict[str, Any] | None
    stdout_tail: str
    stderr_tail: str

    @property
    def green(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def _resolve_in_workspace(workspace: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``workspace``; None if it escapes the boundary."""
    target = (workspace / rel).resolve()
    root = workspace.resolve()
    if target == root or root in target.parents:
        return target
    return None


async def run_experiment_file(
    workspace: Path, rel_path: str, *, timeout_seconds: float = _DEFAULT_TIMEOUT
) -> ExperimentRun:
    """Execute ``python <rel_path>`` in the sandbox; capture a structured run.

    The deterministic core both the tool and the orchestrator's oracle
    call — kept free of ToolResult shaping so it tests directly.
    """
    target = _resolve_in_workspace(workspace, rel_path)
    if target is None or not target.is_file():
        return ExperimentRun(
            path=rel_path,
            returncode=None,
            timed_out=False,
            metrics=None,
            stdout_tail="",
            stderr_tail=f"{rel_path}: file does not exist in the workspace",
        )
    result = await SubprocessSandbox().run(
        f"python {json.dumps(rel_path)}",
        cwd=workspace,
        timeout_seconds=timeout_seconds,
    )
    return ExperimentRun(
        path=rel_path,
        returncode=result.returncode,
        timed_out=result.timed_out,
        metrics=extract_metrics(result.stdout),
        stdout_tail=result.stdout[-_OUTPUT_TAIL:],
        stderr_tail=result.stderr[-_OUTPUT_TAIL:],
    )


class RunExperimentInput(BaseModel):
    """Arguments for ``run_experiment``."""

    path: str = Field(
        description="Path to the experiment script, relative to the workspace "
        "(e.g. 'experiment.py')."
    )


class RunExperimentTool(BaseTool):
    """Run a Python experiment script and report its exit + JSON metrics."""

    name = "run_experiment"
    description = (
        "Execute a Python experiment script in the sandbox. Returns its exit "
        "code, stdout/stderr, and the metrics parsed from the final JSON line "
        "the script prints. Use it to iterate until the experiment runs green."
    )
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=150.0)
    input_model = RunExperimentInput

    async def execute(
        self, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        params = RunExperimentInput.model_validate(input)
        if _resolve_in_workspace(ctx.working_dir, params.path) is None:
            return ToolResult(
                content=f"{params.path} resolves outside the workspace.",
                is_error=True,
                metadata={
                    "root_cause": "path escapes the workspace boundary",
                    "safe_retry": "use a path inside the workspace",
                    "stop_condition": "do not retry the same out-of-tree path",
                },
            )
        run = await run_experiment_file(ctx.working_dir, params.path)
        if run.returncode is None and not run.timed_out:
            return ToolResult(
                content=run.stderr_tail or f"{params.path} does not exist.",
                is_error=True,
                metadata={
                    "root_cause": f"{params.path} does not exist in the workspace",
                    "safe_retry": "write the script first, then run it",
                    "stop_condition": "do not run a path you have not created",
                },
            )
        body = (
            f"returncode={run.returncode} timed_out={run.timed_out}\n"
            f"--- stdout ---\n{run.stdout_tail}\n--- stderr ---\n{run.stderr_tail}"
        )
        if not run.green:
            return ToolResult(
                content=body,
                is_error=True,
                metadata={
                    "returncode": run.returncode,
                    "metrics": run.metrics,
                    "root_cause": "experiment exited non-zero or timed out",
                    "safe_retry": "read the traceback, fix the script, run again",
                    "stop_condition": "after 3 failed runs, simplify the experiment",
                },
            )
        if run.metrics is None:
            return ToolResult(
                content=body
                + "\n\n(no JSON metrics line found — print one final JSON object)",
                metadata={
                    "warning": True,
                    "returncode": 0,
                    "metrics": None,
                    "summary": "ran green but printed no metrics JSON",
                },
            )
        return ToolResult(
            content=body,
            metadata={
                "returncode": 0,
                "metrics": run.metrics,
                "summary": f"green; metrics={run.metrics}",
            },
        )


class SaveArtifactInput(BaseModel):
    """Arguments for ``save_artifact``."""

    name: str = Field(
        description="Artifact filename, relative to the workspace "
        "(e.g. 'problem.md', 'paper.md')."
    )
    markdown: str = Field(min_length=20, description="The artifact body.")


class SaveArtifactTool(BaseTool):
    """Write a named research artifact into the workspace."""

    name = "save_artifact"
    description = (
        "Save a research artifact (problem.md, related_work.md, analysis.md, "
        "paper.md, review.md, ...) into the workspace."
    )
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = SaveArtifactInput

    async def execute(
        self, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        params = SaveArtifactInput.model_validate(input)
        target = _resolve_in_workspace(ctx.working_dir, params.name)
        if target is None:
            return ToolResult(
                content=f"{params.name} resolves outside the workspace.",
                is_error=True,
                metadata={
                    "root_cause": "path escapes the workspace boundary",
                    "safe_retry": "use a plain filename inside the workspace",
                    "stop_condition": "do not retry the same out-of-tree path",
                },
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(params.markdown, encoding="utf-8")
        rel = target.relative_to(ctx.working_dir.resolve()).as_posix()
        return ToolResult(
            content=f"Saved {rel} ({len(params.markdown)} chars).",
            metadata={
                "summary": f"saved {rel}",
                "bytes_written": len(params.markdown.encode("utf-8")),
                "artifacts": [rel],
            },
        )
