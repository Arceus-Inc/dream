"""Default ``web_fetch`` tool -- direct, dependency-free HTTP fetch.

The lean sibling of the Tavily-backed ``web_search`` / ``web_extract``: where
those need an API key and a browser needs CDP, ``web_fetch`` fetches an
arbitrary ``http(s)://`` URL over ``httpx`` (egress already in the SDK) and
returns the page body as readable text -- no key, no headless browser, no
external renderer.

Because it takes an *arbitrary* URL it is SSRF-sensitive, so every target is
passed through :func:`dream.utils.network_guard.guard_web_url` (deny
private/reserved address space) before any bytes are exchanged. That is what
lets it run with zero infra where the browser-backed path cannot: real
``browser_run`` needs a Chromium CDP endpoint, ``web_fetch`` needs only network
egress -- the cheap fast path and the no-CDP fallback for simple page reads.

``risk="external"``, ``tier_required=2`` (same ``REPO_WRITE_NET`` ceiling as
the other web tools). ``network_host`` is reported per-call so the permission
gate sees the concrete target.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from typing import Any

import httpx
from pydantic import BaseModel, Field

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin._errors import tool_error
from dream.utils.network_guard import NetworkGuardError, guard_web_url

_DEFAULT_TIMEOUT = 20.0
_MAX_CHARS = 12_000
_MAX_REDIRECTS = 5
_USER_AGENT = "dream-web-fetch/1.0"

# Many sites serve HTML; the guard only allows http(s), but a body can still be
# ``text/plain`` or JSON -- we only strip tags for HTML content types.
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")


class WebFetchInput(BaseModel):
    """Arguments for the ``web_fetch`` tool."""

    url: str = Field(description="Absolute http(s):// URL to fetch and read.")
    timeout_seconds: float = Field(
        default=_DEFAULT_TIMEOUT, gt=0, le=120, description="Request timeout in seconds. "
    )
    max_chars: int = Field(
        default=_MAX_CHARS, gt=0, le=200_000, description="Maximum characters to return."
    )
    allow_private: bool = Field(
        default=False,
        description=(
            "Opt-in to targets in private/local address space (http://localhost, "
            "10.x, etc.) -- meant for local development only."
        ),
    )


class WebFetchTool(BaseTool):
    """Fetch an http(s) URL and return its text content (SSRF-guarded)."""

    name = "web_fetch"
    description = (
        "Fetch a single http(s) URL and return its readable text content "
        "(HTML tags stripped). Use this as the cheap, browser-free alternative "
        "to web_search/web_extract: no API key and no Chromium needed. Refuses "
        "private/local addresses unless allow_private is explicitly true."
    )
    declaration = ToolDeclaration(
        risk="external", tier_required=2, timeout_seconds=_DEFAULT_TIMEOUT
    )
    input_model = WebFetchInput

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        guard: Any = None,
    ) -> None:
        self._transport = transport
        self._guard = guard or guard_web_url

    def effects_for(self, input: dict[str, Any]) -> ToolEffects:
        """Report the concrete target host for the NETWORK tier ceiling."""
        return ToolEffects(network_host=_resolve_hostname(input.get("url", "")))

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        del ctx
        args = WebFetchInput.model_validate(input)

        try:
            url = await asyncio.to_thread(self._guard, args.url, allow_private=args.allow_private)
        except NetworkGuardError as exc:
            return tool_error(
                f"web_fetch refused: {exc}.",
                root_cause=str(exc),
                safe_retry="provide a public http(s) URL, or pass allow_private=True for local dev",
                stop_condition="stop fetching targets the guard refuses",
            )

        try:
            response = await self._get(url, timeout_seconds=args.timeout_seconds)
        except httpx.HTTPError as exc:
            return tool_error(
                f"web_fetch failed: could not reach URL ({exc}).",
                root_cause=f"HTTP transport error: {exc}",
                safe_retry="retry once; transient network errors are common",
                stop_condition="stop after two consecutive transport failures",
            )

        if response.status_code >= 400:
            return tool_error(
                f"web_fetch failed: URL returned HTTP {response.status_code}.",
                root_cause=f"target returned status {response.status_code}",
                safe_retry="check the URL, then retry once",
                stop_condition="stop after two consecutive non-2xx responses",
            )

        content_type = (response.headers.get("content-type") or "").lower()
        body = self._body_text(response, content_type=content_type)
        body = body[: args.max_chars - 1].rstrip() + "…" if len(body) > args.max_chars else body

        return ToolResult(
            content=body or "(empty body)",
            structured={
                "url": url,
                "status_code": response.status_code,
                "content_type": content_type or None,
                "char_len": len(body),
            },
            metadata={
                "url": url,
                "status_code": response.status_code,
                "char_len": len(body),
                "truncated": len(body) == args.max_chars,
                "summary": f"fetched {url} ({response.status_code}, {len(body)} chars)",
            },
        )

    async def _get(self, url: str, *, timeout_seconds: float) -> httpx.Response:
        """Issue one GET. Isolating here so tests can stub the transport."""
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout_seconds,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            headers={"user-agent": _USER_AGENT},
            trust_env=False,
        ) as client:
            return await client.get(url)

    @staticmethod
    def _body_text(response: httpx.Response, *, content_type: str) -> str:
        if content_type in _HTML_CONTENT_TYPES:
            return _html_to_text(response.text)
        return " ".join(response.text.split())


def _resolve_hostname(raw: str | None) -> str | None:
    from urllib.parse import urlparse

    parsed = urlparse(raw or "")
    return parsed.hostname


def _html_to_text(html: str) -> str:
    """Strip HTML to readable text using only the stdlib parser."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return " ".join("".join(parser.text).split())


class _TextExtractor(HTMLParser):
    """HTMLParser accumulating visible text, inserting newlines at block tags."""

    _BLOCK_TAGS = frozenset(
        {
            "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5",
            "h6", "section", "article", "blockquote", "pre", "tr", "table",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self._skip: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "template", "svg", "head"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in self._BLOCK_TAGS:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template", "svg", "head"}:
            self._skip = max(0, self._skip - 1)
            return
        if not self._skip and tag in self._BLOCK_TAGS:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.text.append(data)


__all__ = ["WebFetchInput", "WebFetchTool"]