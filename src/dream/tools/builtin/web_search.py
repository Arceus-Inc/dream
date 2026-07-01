"""Default ``web_search`` tool — Tavily-backed web search.

External (``risk="external"``): the call leaves the machine for Tavily's API,
so it reports ``network_host`` through :meth:`effects_for` and requires the
network sandbox tier (``REPO_WRITE_NET``). It is *not* read-only — network
egress is always gated even though the search itself mutates nothing.

The API key is a tool secret, not a model-provider credential, so it is read
lazily from the environment (``DREAM_TAVILY_API_KEY`` first, then the vendor
``TAVILY_API_KEY``) rather than the provider auth map. A missing key is the
operator's mistake and surfaces the Spec 05 three-part error contract, not a
crash — sessions without a key keep working, they just can't search.

Tavily is a single trusted endpoint, so no SSRF network-guard is needed here
(unlike a fetch-arbitrary-URL tool). ``base_url`` / ``transport`` are injectable
purely so tests can stub the HTTP hop without touching the network.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error

_TAVILY_URL = "https://api.tavily.com/search"
_TAVILY_HOST = "api.tavily.com"
_API_KEY_ENV: tuple[str, ...] = ("DREAM_TAVILY_API_KEY", "TAVILY_API_KEY")
_TIMEOUT_SECONDS = 30.0
_SNIPPET_MAX = 500


def _resolve_api_key(override: str | None) -> str | None:
    """Return the first non-empty key: explicit override, then env candidates."""
    if override and override.strip():
        return override.strip()
    for name in _API_KEY_ENV:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


class WebSearchInput(BaseModel):
    """Arguments for the ``web_search`` tool."""

    query: str = Field(description="The search query.")
    max_results: int = Field(
        default=5, ge=1, le=20, description="Maximum number of results to return."
    )
    topic: Literal["general", "news"] = Field(
        default="general",
        description="Search topic. Use 'news' for recent/current events.",
    )
    search_depth: Literal["basic", "advanced"] = Field(
        default="basic",
        description="'advanced' is slower but retrieves more relevant content.",
    )
    include_answer: bool = Field(
        default=True,
        description="Ask Tavily to synthesize a short answer from the results.",
    )
    include_domains: list[str] = Field(
        default_factory=list,
        description="Restrict results to these domains (e.g. ['arxiv.org']).",
    )
    exclude_domains: list[str] = Field(
        default_factory=list,
        description="Never return results from these domains.",
    )


class WebSearchTool(BaseTool):
    """Search the web via Tavily and return a compact, cited result block."""

    name = "web_search"
    description = (
        "Search the web for current information and return the top results with "
        "titles, URLs, and snippets (optionally a synthesized answer). Use this "
        "for facts that may have changed or are outside the training data."
    )
    declaration = ToolDeclaration(
        risk="external", tier_required=2, timeout_seconds=_TIMEOUT_SECONDS
    )
    input_model = WebSearchInput

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = _TAVILY_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._transport = transport

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        """Report Tavily's host so the gate applies the NETWORK tier ceiling."""
        del input
        return ToolEffects(network_host=_TAVILY_HOST)

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        del ctx
        args = WebSearchInput.model_validate(input)

        key = _resolve_api_key(self._api_key)
        if key is None:
            return tool_error(
                "web_search is unavailable: no Tavily API key is configured.",
                root_cause="neither DREAM_TAVILY_API_KEY nor TAVILY_API_KEY is set",
                safe_retry="set DREAM_TAVILY_API_KEY in the environment, then retry",
                stop_condition="stop calling web_search until a key is configured",
            )

        payload: dict[str, Any] = {
            "api_key": key,
            "query": args.query,
            "max_results": args.max_results,
            "topic": args.topic,
            "search_depth": args.search_depth,
            "include_answer": args.include_answer,
        }
        if args.include_domains:
            payload["include_domains"] = args.include_domains
        if args.exclude_domains:
            payload["exclude_domains"] = args.exclude_domains

        try:
            response = await self._post(payload)
        except httpx.HTTPError as exc:
            return tool_error(
                f"web_search failed: could not reach Tavily ({exc}).",
                root_cause=f"HTTP transport error: {exc}",
                safe_retry="retry once; transient network errors are common",
                stop_condition="stop after two consecutive transport failures",
            )

        if response.status_code == 401:
            return tool_error(
                "web_search failed: Tavily rejected the API key (401).",
                root_cause="the configured Tavily API key is invalid or revoked",
                safe_retry="fix DREAM_TAVILY_API_KEY, then retry",
                stop_condition="do not retry with the same key",
            )
        if response.status_code == 429:
            return tool_error(
                "web_search failed: Tavily rate limit reached (429).",
                root_cause="too many requests to Tavily in the current window",
                safe_retry="wait and retry; consider fewer searches",
                stop_condition="stop after repeated 429s",
            )
        if response.status_code >= 400:
            return tool_error(
                f"web_search failed: Tavily returned HTTP {response.status_code}.",
                root_cause=f"unexpected Tavily status {response.status_code}",
                safe_retry="check the query arguments, then retry once",
                stop_condition="stop after two consecutive non-2xx responses",
            )

        try:
            data = response.json()
        except ValueError as exc:
            return tool_error(
                "web_search failed: Tavily response was not valid JSON.",
                root_cause=f"JSON decode error: {exc}",
                safe_retry="retry once",
                stop_condition="stop after a second malformed response",
            )

        results = data.get("results") or []
        answer = (data.get("answer") or "").strip()
        content = _render(args.query, answer, results)
        return ToolResult(
            content=content,
            structured={"answer": answer or None, "results": results},
            metadata={
                "result_count": len(results),
                "summary": (
                    f"{len(results)} web result(s) for {args.query!r}"
                    if results
                    else f"no web results for {args.query!r}"
                ),
            },
        )

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        """Issue one POST to Tavily. Isolated so tests can stub the transport."""
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=_TIMEOUT_SECONDS,
            trust_env=False,
        ) as client:
            return await client.post(self._base_url, json=payload)


def _render(query: str, answer: str, results: list[dict[str, Any]]) -> str:
    lines = [f"Search results for: {query}"]
    if answer:
        lines.append(f"\nAnswer: {answer}")
    if not results:
        lines.append("\nNo results found.")
        return "\n".join(lines)
    lines.append("")
    for index, result in enumerate(results, start=1):
        title = str(result.get("title") or "(untitled)").strip()
        url = str(result.get("url") or "").strip()
        lines.append(f"{index}. {title}")
        if url:
            lines.append(f"   URL: {url}")
        snippet = _clip(str(result.get("content") or "").strip())
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


def _clip(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > _SNIPPET_MAX:
        return collapsed[: _SNIPPET_MAX - 1].rstrip() + "…"
    return collapsed


__all__ = ["WebSearchInput", "WebSearchTool"]
