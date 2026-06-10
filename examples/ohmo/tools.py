"""Ohmo's research tools — the agent's action space beyond the SDK defaults.

Three micro-tools, each with a strict schema (no catch-all "do research"
door):

- ``arxiv_search`` — query the arXiv export API (the host is hardcoded;
  the model never controls the URL, only the query terms).
- ``save_research_brief`` — write one brief under
  ``docs/research/briefs/`` and maintain ``docs/research/INDEX.md``.
- ``reading_queue`` — durable cross-session queue at
  ``docs/research/queue.json``.

Parsing and file mechanics are plain functions so they test without a
network or an engine.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext

__all__ = [
    "ArxivEntry",
    "ArxivSearchTool",
    "ReadingQueueTool",
    "SaveResearchBriefTool",
    "format_entries",
    "parse_arxiv_feed",
    "research_tools",
]

ARXIV_API_URL = "https://export.arxiv.org/api/query"
BRIEFS_DIR = Path("docs/research/briefs")
INDEX_PATH = Path("docs/research/INDEX.md")
QUEUE_PATH = Path("docs/research/queue.json")

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")
_SUMMARY_LIMIT = 900
_MAX_QUEUE_ITEMS = 200


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


# ---------------------------------------------------------------------------
# save_research_brief
# ---------------------------------------------------------------------------


class SaveBriefInput(BaseModel):
    """Arguments for ``save_research_brief``."""

    slug: str = Field(
        description="Filename slug, lowercase letters/digits/hyphens, e.g. 'mamba-2-ssm'."
    )
    title: str = Field(min_length=3, max_length=200, description="Human title for the index.")
    markdown: str = Field(
        min_length=50, description="The full brief body in markdown."
    )
    revise: bool = Field(
        default=False,
        description="Set true to deliberately overwrite an existing brief.",
    )


class SaveResearchBriefTool(BaseTool):
    """Write one research brief and keep the index current."""

    name = "save_research_brief"
    description = (
        "Save a research brief to docs/research/briefs/{slug}.md and record it "
        "in docs/research/INDEX.md. Refuses to overwrite unless revise=true."
    )
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=10.0)
    input_model = SaveBriefInput

    async def execute(
        self, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        params = SaveBriefInput.model_validate(input)
        if not _SLUG_RE.match(params.slug):
            return ToolResult(
                content=f"Invalid slug {params.slug!r}.",
                is_error=True,
                metadata={
                    "root_cause": "slug must match [a-z0-9][a-z0-9-]{1,80}",
                    "safe_retry": "retry with a lowercase hyphenated slug",
                    "stop_condition": "do not retry the same slug",
                },
            )
        brief_path = ctx.working_dir / BRIEFS_DIR / f"{params.slug}.md"
        if brief_path.exists() and not params.revise:
            return ToolResult(
                content=f"Brief {params.slug} already exists.",
                is_error=True,
                metadata={
                    "root_cause": f"{brief_path} exists and revise=false",
                    "safe_retry": "pass revise=true to revise, or pick a new slug",
                    "stop_condition": "do not blind-retry with the same arguments",
                },
            )
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(params.markdown, encoding="utf-8")
        _index_add(ctx.working_dir / INDEX_PATH, slug=params.slug, title=params.title)
        rel = brief_path.relative_to(ctx.working_dir).as_posix()
        return ToolResult(
            content=f"Saved brief {params.slug} ({len(params.markdown)} chars) to {rel}.",
            metadata={
                "summary": f"brief saved: {params.slug}",
                "bytes_written": len(params.markdown.encode("utf-8")),
                "artifacts": [rel, INDEX_PATH.as_posix()],
            },
        )


def _index_add(index_path: Path, *, slug: str, title: str) -> None:
    """Append one index line, creating the index on first use; idempotent."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"- [{title}](briefs/{slug}.md)"
    if index_path.exists():
        body = index_path.read_text(encoding="utf-8")
        if f"(briefs/{slug}.md)" in body:
            return
        if not body.endswith("\n"):
            body += "\n"
        index_path.write_text(body + line + "\n", encoding="utf-8")
        return
    index_path.write_text(f"# Research briefs\n\n{line}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# reading_queue
# ---------------------------------------------------------------------------


class ReadingQueueInput(BaseModel):
    """Arguments for ``reading_queue``."""

    action: Literal["add", "list", "done"] = Field(
        description="'add' an item, 'list' the queue, mark the named item 'done'."
    )
    item: str | None = Field(
        default=None,
        max_length=300,
        description="The queue item (required for add/done): an arXiv id or a short topic.",
    )


class ReadingQueueTool(BaseTool):
    """Durable cross-session reading queue."""

    name = "reading_queue"
    description = (
        "Manage the durable reading queue (docs/research/queue.json): add items "
        "to cover later, list pending items, mark items done."
    )
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=5.0)
    input_model = ReadingQueueInput

    async def execute(
        self, input: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        params = ReadingQueueInput.model_validate(input)
        queue_path = ctx.working_dir / QUEUE_PATH
        items = _queue_load(queue_path)
        if params.action == "list":
            if not items:
                return ToolResult(
                    content="Reading queue is empty.",
                    metadata={"summary": "queue empty", "queued": 0},
                )
            listing = "\n".join(f"{n + 1}. {item}" for n, item in enumerate(items))
            return ToolResult(
                content=listing,
                metadata={"summary": f"{len(items)} queued", "queued": len(items)},
            )
        if not params.item or not params.item.strip():
            return ToolResult(
                content=f"action={params.action} requires an item.",
                is_error=True,
                metadata={
                    "root_cause": "missing 'item' argument",
                    "safe_retry": "retry with item set",
                    "stop_condition": "do not retry without an item",
                },
            )
        item = params.item.strip()
        if params.action == "add":
            if item in items:
                return ToolResult(
                    content=f"Already queued: {item}",
                    metadata={"summary": "duplicate ignored", "queued": len(items)},
                )
            items.append(item)
            _queue_save(queue_path, items[-_MAX_QUEUE_ITEMS:])
            return ToolResult(
                content=f"Queued: {item}",
                metadata={"summary": f"queued ({len(items)} total)", "queued": len(items)},
            )
        # action == "done"
        if item not in items:
            return ToolResult(
                content=f"Not in queue: {item}",
                is_error=True,
                metadata={
                    "root_cause": "item is not in the queue",
                    "safe_retry": "reading_queue list to see exact items",
                    "stop_condition": "do not retry the same missing item",
                },
            )
        items.remove(item)
        _queue_save(queue_path, items)
        return ToolResult(
            content=f"Done: {item} ({len(items)} remaining)",
            metadata={"summary": f"done ({len(items)} remaining)", "queued": len(items)},
        )


def _queue_load(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _queue_save(path: Path, items: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")


def research_tools() -> tuple[BaseTool, ...]:
    """Ohmo's tool bundle, ready to register into a ``ToolRegistry``."""
    return (ArxivSearchTool(), SaveResearchBriefTool(), ReadingQueueTool())
