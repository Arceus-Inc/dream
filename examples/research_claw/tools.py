"""research_claw tools — the experiment oracle and artifact writer.

``run_experiment`` is the load-bearing one: it executes a generated
Python script in dream's :class:`~dream.sandbox.SubprocessSandbox` (the
real thing, tree-killed on timeout) and parses a JSON metrics line from
its stdout. The model uses it to iterate code until it runs green; the
orchestrator then runs it once more, authoritatively, as the oracle.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.sandbox import SubprocessSandbox
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

__all__ = [
    "ArxivSearchTool",
    "ExperimentRun",
    "RunExperimentTool",
    "SaveArtifactTool",
    "extract_metrics",
    "run_experiment_file",
]

# arXiv search (moved from the deleted ohmo example — research_claw's only
# other consumer).
ARXIV_API_URL = "https://export.arxiv.org/api/query"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_SUMMARY_LIMIT = 900



# ---------------------------------------------------------------------------
# arXiv feed parsing (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArxivEntry:
    """One paper from an arXiv Atom feed."""

    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    summary: str
    published: str
    link: str


def parse_arxiv_feed(xml_text: str) -> tuple[ArxivEntry, ...]:
    """Parse an arXiv export Atom feed; tolerate odd entries by skipping them."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ()
    entries: list[ArxivEntry] = []
    for node in root.findall("atom:entry", _ATOM_NS):
        entry = _parse_entry(node)
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def _parse_entry(node: ET.Element) -> ArxivEntry | None:
    raw_id = _text(node, "atom:id")
    title = _text(node, "atom:title")
    if not raw_id or not title:
        return None
    authors = tuple(
        name
        for author in node.findall("atom:author", _ATOM_NS)
        if (name := _text(author, "atom:name"))
    )
    return ArxivEntry(
        arxiv_id=raw_id.rsplit("/abs/", 1)[-1],
        title=_squash(title),
        authors=authors,
        summary=_squash(_text(node, "atom:summary") or ""),
        published=_text(node, "atom:published") or "",
        link=raw_id,
    )


def _text(node: ET.Element, tag: str) -> str | None:
    child = node.find(tag, _ATOM_NS)
    return child.text.strip() if child is not None and child.text else None


def _squash(text: str) -> str:
    return " ".join(text.split())


def format_entries(entries: tuple[ArxivEntry, ...]) -> str:
    """Render entries for the model: id, title, authors, date, trimmed abstract."""
    blocks: list[str] = []
    for entry in entries:
        summary = entry.summary
        if len(summary) > _SUMMARY_LIMIT:
            summary = summary[:_SUMMARY_LIMIT] + "…"
        authors = ", ".join(entry.authors[:6]) + (
            ", et al." if len(entry.authors) > 6 else ""
        )
        blocks.append(
            f"[{entry.arxiv_id}] {entry.title}\n"
            f"  authors: {authors}\n"
            f"  published: {entry.published}\n"
            f"  link: {entry.link}\n"
            f"  abstract: {summary}"
        )
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# arxiv_search
# ---------------------------------------------------------------------------

FetchFn = Callable[[str], Awaitable[str]]


async def _http_fetch(url: str) -> str:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


class ArxivSearchInput(BaseModel):
    """Arguments for ``arxiv_search``."""

    query: str = Field(
        min_length=2,
        max_length=300,
        description=(
            "arXiv search terms, e.g. 'state space models' or "
            "'cat:cs.LG AND ti:diffusion'."
        ),
    )
    max_results: int = Field(
        default=8, ge=1, le=25, description="How many results to return (1-25)."
    )
    sort_by: Literal["submittedDate", "relevance"] = Field(
        default="submittedDate",
        description="'submittedDate' for the newest papers, 'relevance' for the best match.",
    )


class ArxivSearchTool(BaseTool):
    """Search arXiv and return papers with abstracts.

    The endpoint host is pinned to ``export.arxiv.org``; the model only
    controls the query string, so this never becomes a generic fetcher.
    """

    name = "arxiv_search"
    description = (
        "Search arXiv for papers. Returns id, title, authors, date, link and "
        "abstract per result. Use sort_by='submittedDate' to find new papers."
    )
    declaration = ToolDeclaration(risk="external", tier_required=2, timeout_seconds=30.0)
    input_model = ArxivSearchInput

    def __init__(self, fetch: FetchFn = _http_fetch) -> None:
        self._fetch = fetch

    async def execute(
        self, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        params = ArxivSearchInput.model_validate(input)
        url = ARXIV_API_URL + "?" + urlencode(
            {
                "search_query": f"all:{params.query}"
                if ":" not in params.query
                else params.query,
                "max_results": params.max_results,
                "sortBy": params.sort_by,
                "sortOrder": "descending",
            }
        )
        try:
            body = await self._fetch(url)
        except Exception as exc:
            return ToolResult(
                content=f"arXiv query failed: {exc}",
                is_error=True,
                metadata={
                    "root_cause": f"arXiv API request failed: {exc}",
                    "safe_retry": "retry once; if it fails again, narrow the query",
                    "stop_condition": "stop after two consecutive failures",
                },
            )
        entries = parse_arxiv_feed(body)
        if not entries:
            return ToolResult(
                content="No papers matched.",
                metadata={"summary": "0 results", "results": 0},
            )
        return ToolResult(
            content=format_entries(entries),
            metadata={"summary": f"{len(entries)} paper(s)", "results": len(entries)},
        )

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
