"""Digest tools — Hacker News search and timestamped file delivery.

Same discipline as ohmo's tools: pinned hosts (the model controls query
terms, never URLs), strict schemas, 3-part error contracts, and pure
helpers that test offline. arXiv search is reused from ``ohmo.tools``.

Delivery is a file, not email: each run drops a timestamped markdown file
under ``research_ideas/`` so a 2-hourly cadence builds a browsable log.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

__all__ = [
    "HnSearchTool",
    "SaveDigestTool",
    "format_hn_hits",
    "parse_hn_hits",
]

HN_API_URL = "https://hn.algolia.com/api/v1/search_by_date"
RESEARCH_IDEAS_DIR = Path("research_ideas")

_TITLE_LIMIT = 200
_SECONDS_PER_HOUR = 3_600


# ---------------------------------------------------------------------------
# Hacker News search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HnHit:
    """One story from the Algolia HN API."""

    title: str
    url: str
    points: int
    comments: int
    created_at: str


def parse_hn_hits(body: str) -> tuple[HnHit, ...]:
    """Parse an Algolia response; skip malformed hits rather than failing."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return ()
    raw_hits = data.get("hits")
    if not isinstance(raw_hits, list):
        return ()
    hits: list[HnHit] = []
    for raw in raw_hits:
        if not isinstance(raw, dict) or not raw.get("title"):
            continue
        object_id = raw.get("objectID", "")
        hits.append(
            HnHit(
                title=str(raw["title"])[:_TITLE_LIMIT],
                url=str(
                    raw.get("url")
                    or f"https://news.ycombinator.com/item?id={object_id}"
                ),
                points=int(raw.get("points") or 0),
                comments=int(raw.get("num_comments") or 0),
                created_at=str(raw.get("created_at") or ""),
            )
        )
    return tuple(hits)


def format_hn_hits(hits: tuple[HnHit, ...]) -> str:
    return "\n".join(
        f"- {hit.title} ({hit.points} points, {hit.comments} comments, "
        f"{hit.created_at})\n  {hit.url}"
        for hit in hits
    )


FetchFn = Callable[[str], Awaitable[str]]


async def _http_fetch(url: str) -> str:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


class HnSearchInput(BaseModel):
    """Arguments for ``hn_search``."""

    query: str = Field(min_length=2, max_length=200, description="Search terms.")
    hours: int = Field(
        default=2,
        ge=1,
        le=168,
        description="How many hours back to search (default 2 — the digest window).",
    )
    max_results: int = Field(default=20, ge=1, le=50)


class HnSearchTool(BaseTool):
    """Search recent Hacker News stories (Algolia API, host pinned)."""

    name = "hn_search"
    description = (
        "Search Hacker News stories from the last N hours. Returns title, "
        "points, comment count and link per story. Default window is 2 hours."
    )
    declaration = ToolDeclaration(risk="external", tier_required=2, timeout_seconds=30.0)
    input_model = HnSearchInput

    def __init__(self, fetch: FetchFn = _http_fetch) -> None:
        self._fetch = fetch

    async def execute(
        self, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        params = HnSearchInput.model_validate(input)
        cutoff = int(time.time()) - params.hours * _SECONDS_PER_HOUR
        url = (
            f"{HN_API_URL}?query={httpx.QueryParams({'q': params.query})['q']}"
            f"&tags=story&hitsPerPage={params.max_results}"
            f"&numericFilters=created_at_i>{cutoff}"
        )
        try:
            body = await self._fetch(url)
        except Exception as exc:
            return ToolResult(
                content=f"Hacker News query failed: {exc}",
                is_error=True,
                metadata={
                    "root_cause": f"HN API request failed: {exc}",
                    "safe_retry": "retry once; then continue with arXiv only",
                    "stop_condition": "stop after two consecutive failures",
                },
            )
        hits = parse_hn_hits(body)
        if not hits:
            return ToolResult(
                content=f"No stories in the last {params.hours}h.",
                metadata={"summary": "0 stories", "results": 0},
            )
        return ToolResult(
            content=format_hn_hits(hits),
            metadata={"summary": f"{len(hits)} stories", "results": len(hits)},
        )


# ---------------------------------------------------------------------------
# File delivery
# ---------------------------------------------------------------------------


class SaveDigestInput(BaseModel):
    """Arguments for ``save_digest``."""

    title: str = Field(min_length=5, max_length=200)
    markdown: str = Field(
        min_length=40, description="The complete digest body in markdown."
    )


class SaveDigestTool(BaseTool):
    """Write the digest to ``research_ideas/{timestamp}.md`` — call once.

    The filename timestamp is fixed at construction (the run's stamp), so
    every 2-hourly run produces a distinct, sortable file and a re-call
    within the same run overwrites rather than littering.
    """

    name = "save_digest"
    description = (
        "Save the finished digest to research_ideas/{timestamp}.md. "
        "Call exactly once per run — this is the deliverable."
    )
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = SaveDigestInput

    def __init__(self, *, stamp: str) -> None:
        self._stamp = stamp

    async def execute(
        self, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        params = SaveDigestInput.model_validate(input)
        out = ctx.working_dir / RESEARCH_IDEAS_DIR / f"{self._stamp}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            f"# {params.title}\n\n_Generated {self._stamp}_\n\n{params.markdown}\n",
            encoding="utf-8",
        )
        rel = out.relative_to(ctx.working_dir).as_posix()
        return ToolResult(
            content=f"Digest saved to {rel} ({len(params.markdown)} chars).",
            metadata={
                "summary": f"saved {rel}",
                "bytes_written": len(params.markdown.encode("utf-8")),
                "artifacts": [rel],
            },
        )
