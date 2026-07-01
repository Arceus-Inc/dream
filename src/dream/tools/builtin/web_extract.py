"""Default ``web_extract`` tool — Tavily-backed page fetch + clean extraction.

Sibling of ``web_search`` (same Tavily endpoint family, same key resolution, same
single-host egress — Tavily fetches the page, not us, so no SSRF guard is needed).
Where ``web_search`` *finds* URLs, ``web_extract`` *reads* them: it returns the
cleaned main content of one or more pages, plus a per-URL signal (``needs_render``)
a research agent uses to decide whether a page is a JS shell that warrants a
browser fallback.
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

_TAVILY_URL = "https://api.tavily.com/extract"
_TAVILY_HOST = "api.tavily.com"
_API_KEY_ENV: tuple[str, ...] = ("DREAM_TAVILY_API_KEY", "TAVILY_API_KEY")
_TIMEOUT_SECONDS = 60.0          # extract (esp. advanced, multi-URL) is slower than search
_PREVIEW_MAX = 2000              # per-URL preview in the human-readable block
_THIN_CONTENT = 200              # below this, treat as a likely JS shell / failed read
_JS_MARKERS: tuple[str, ...] = (
    "enable javascript",
    "you need to enable javascript",
    "javascript is required",
    "requires javascript to run",
    "please turn on javascript",
)


def _resolve_api_key(override: str | None) -> str | None:
    """Return the first non-empty key: explicit override, then env candidates."""
    if override and override.strip():
        return override.strip()
    for name in _API_KEY_ENV:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


class WebExtractInput(BaseModel):
    """Arguments for the ``web_extract`` tool."""

    urls: list[str] = Field(
        description="One or more URLs to fetch and extract clean content from.",
        min_length=1,
        max_length=20,
    )
    extract_depth: Literal["basic", "advanced"] = Field(
        default="basic",
        description="'advanced' retrieves more content (tables, deeper DOM) at higher cost.",
    )
    format: Literal["markdown", "text"] = Field(
        default="markdown",
        description="Content format: cleaned markdown (default) or plain text.",
    )
    include_images: bool = Field(
        default=False,
        description="Also return image URLs found on the page.",
    )


class WebExtractTool(BaseTool):
    """Fetch one or more URLs via Tavily and return cleaned, readable content."""

    name = "web_extract"
    description = (
        "Fetch one or more web pages and return their cleaned main content (article "
        "text with nav/ads/boilerplate stripped). Use this to READ a page you found "
        "with web_search. Each result carries a `needs_render` flag: when true, the "
        "page is a JS shell or came back empty/thin, so escalate to a rendering "
        "browser or try a different source."
    )
    declaration = ToolDeclaration(
        risk="external", tier_required=2, timeout_seconds=_TIMEOUT_SECONDS
    )
    input_model = WebExtractInput

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
        args = WebExtractInput.model_validate(input)

        key = _resolve_api_key(self._api_key)
        if key is None:
            return tool_error(
                "web_extract is unavailable: no Tavily API key is configured.",
                root_cause="neither DREAM_TAVILY_API_KEY nor TAVILY_API_KEY is set",
                safe_retry="set DREAM_TAVILY_API_KEY in the environment, then retry",
                stop_condition="stop calling web_extract until a key is configured",
            )

        payload: dict[str, Any] = {
            "api_key": key,
            "urls": args.urls,
            "extract_depth": args.extract_depth,
            "format": args.format,
            "include_images": args.include_images,
        }

        try:
            response = await self._post(payload)
        except httpx.HTTPError as exc:
            return tool_error(
                f"web_extract failed: could not reach Tavily ({exc}).",
                root_cause=f"HTTP transport error: {exc}",
                safe_retry="retry once; transient network errors are common",
                stop_condition="stop after two consecutive transport failures",
            )

        if response.status_code == 401:
            return tool_error(
                "web_extract failed: Tavily rejected the API key (401).",
                root_cause="the configured Tavily API key is invalid or revoked",
                safe_retry="fix DREAM_TAVILY_API_KEY, then retry",
                stop_condition="do not retry with the same key",
            )
        if response.status_code == 429:
            return tool_error(
                "web_extract failed: Tavily rate limit reached (429).",
                root_cause="too many requests to Tavily in the current window",
                safe_retry="wait and retry; consider fewer URLs per call",
                stop_condition="stop after repeated 429s",
            )
        if response.status_code >= 400:
            return tool_error(
                f"web_extract failed: Tavily returned HTTP {response.status_code}.",
                root_cause=f"unexpected Tavily status {response.status_code}",
                safe_retry="check the URLs, then retry once",
                stop_condition="stop after two consecutive non-2xx responses",
            )

        try:
            data = response.json()
        except ValueError as exc:
            return tool_error(
                "web_extract failed: Tavily response was not valid JSON.",
                root_cause=f"JSON decode error: {exc}",
                safe_retry="retry once",
                stop_condition="stop after a second malformed response",
            )

        raw_results = data.get("results") or []
        failed = data.get("failed_results") or []
        entries = [_signal_entry(r) for r in raw_results]
        for f in failed:  # a URL Tavily could not fetch at all → definitively needs a fallback
            entries.append(
                {
                    "url": str(f.get("url") or "").strip(),
                    "content": "",
                    "content_len": 0,
                    "extraction_ok": False,
                    "needs_render": True,
                    "error": str(f.get("error") or "extraction failed"),
                }
            )

        content = _render(entries)
        any_needs_render = any(e["needs_render"] for e in entries)
        return ToolResult(
            content=content,
            structured={"results": entries, "any_needs_render": any_needs_render},
            metadata={
                "extracted": sum(1 for e in entries if e["extraction_ok"]),
                "needs_render": sum(1 for e in entries if e["needs_render"]),
                "summary": f"extracted {len(raw_results)}/{len(args.urls)} URL(s)",
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


def _signal_entry(result: dict[str, Any]) -> dict[str, Any]:
    """Turn one Tavily result into a content + escalation-signal record."""
    url = str(result.get("url") or "").strip()
    text = str(result.get("raw_content") or "").strip()
    length = len(text)
    lowered = text.lower()
    js_shell = length < _THIN_CONTENT or any(m in lowered for m in _JS_MARKERS)
    return {
        "url": url,
        "content": text,
        "content_len": length,
        "extraction_ok": length > 0,
        # the escalation trigger the research brief branches on:
        "needs_render": js_shell,
        "error": None,
    }


def _render(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "No content extracted."
    lines: list[str] = []
    for i, e in enumerate(entries, start=1):
        lines.append(f"{i}. {e['url']}")
        if not e["extraction_ok"]:
            reason = e.get("error") or "empty/JS-shell — needs a rendering fallback"
            lines.append(f"   [needs_render] {reason}")
            continue
        if e["needs_render"]:
            lines.append("   [needs_render] content is thin/JS-shell — consider a rendering fallback")
        body = e["content"]
        preview = body[: _PREVIEW_MAX - 1].rstrip() + "…" if len(body) > _PREVIEW_MAX else body
        lines.append(f"   {preview}")
    return "\n".join(lines)


__all__ = ["WebExtractInput", "WebExtractTool"]
