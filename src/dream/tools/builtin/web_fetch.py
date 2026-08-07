"""Default ``web_fetch`` tool -- direct, dependency-free HTTP fetch.

The lean sibling of the Tavily-backed ``web_search`` / ``web_extract``: where
those need an API key and a browser needs CDP, ``web_fetch`` fetches an
arbitrary ``http(s)://`` URL over ``httpx`` (egress already in the SDK) and
returns the page body as readable text -- no key, no headless browser, no
external renderer.

Because it takes an *arbitrary* URL it is SSRF-sensitive, so every target --
**including every redirect hop** -- is passed through
:func:`dream.utils.network_guard.guard_web_url` (deny private/reserved address
space) before any bytes are exchanged. Automatic redirects are disabled and
followed by hand so a public URL cannot redirect the fetch onto loopback,
private, or metadata endpoints. That is what lets it run with zero infra where
the browser-backed path cannot: real ``browser_run`` needs a Chromium CDP
endpoint, ``web_fetch`` needs only network egress -- the cheap fast path and the
no-CDP fallback for simple page reads.

``risk="external"``, ``tier_required=2`` (same ``REPO_WRITE_NET`` ceiling as
the other web tools). ``network_host`` is reported per-call so the permission
gate sees the concrete target. The body is streamed and read only up to a
multiple of ``max_chars`` so a huge page cannot balloon worker memory.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

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

# Media types we strip tags for. Comparison is on the parameter-free media type
# (e.g. ``text/html; charset=utf-8`` matches ``text/html``).
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})

# A redirect is only a 3xx with a Location header we actually follow.
_REDIRECT_CODES: frozenset[int] = frozenset({301, 302, 303, 307, 308})

# HTML-to-text shrinks the body, so a capped *byte* read yields fewer text
# characters. Read a few multiples of ``max_chars`` (plus a fixed constant) so a
# requested output limit can actually be reached, while still bounding memory.
_READ_BYTE_MULTIPLIER = 4
_READ_BYTE_FIXED = 16_384


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


class _RedirectRefused(NetworkGuardError):
    """A redirect destination failed the SSRF guard."""


class _TooManyRedirects(Exception):
    """The redirect chain exceeded :data:`_MAX_REDIRECTS` without reaching a body."""


@dataclass(frozen=True)
class _Fetched:
    """The result of one (guarded, redirect-aware) fetch: a final URL + body."""

    url: str
    status_code: int
    media_type: str | None
    encoding: str
    body: bytes


class WebFetchTool(BaseTool):
    """Fetch an http(s) URL and return its text content (SSRF-guarded)."""

    name = "web_fetch"
    description = (
        "Fetch one http(s) URL with a direct SSRF-guarded GET (no API key, no "
        "browser). Prefer this for simple static/HTML pages. Use web_extract for "
        "Tavily-cleaned multi-URL extraction (requires key); use browser_run only "
        "when the page needs JavaScript rendering or interactive/authenticated "
        "flows such as forms or logins."
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
        except (NetworkGuardError, ValueError) as exc:
            return _refused(exc)

        try:
            fetched = await self._fetch(
                url,
                allow_private=args.allow_private,
                timeout_seconds=args.timeout_seconds,
                max_chars=args.max_chars,
            )
        except _RedirectRefused as exc:
            return _refused(exc)
        except _TooManyRedirects as exc:
            return tool_error(
                f"web_fetch failed: redirects exceeded the limit ({_MAX_REDIRECTS}).",
                root_cause=str(exc),
                safe_retry="follow the redirect chain manually or pick the final URL",
                stop_condition="stop fetching URLs with unbounded redirect chains",
            )
        except httpx.HTTPError as exc:
            return tool_error(
                f"web_fetch failed: could not reach URL ({exc}).",
                root_cause=f"HTTP transport error: {exc}",
                safe_retry="retry once; transient network errors are common",
                stop_condition="stop after two consecutive transport failures",
            )

        if fetched.status_code >= 400:
            return tool_error(
                f"web_fetch failed: URL returned HTTP {fetched.status_code}.",
                root_cause=f"target returned status {fetched.status_code}",
                safe_retry="check the URL, then retry once",
                stop_condition="stop after two consecutive non-2xx responses",
            )

        body = _body_text(fetched)
        body = _clamp(body, args.max_chars)

        return ToolResult(
            content=body or "(empty body)",
            structured={
                "url": fetched.url,
                "status_code": fetched.status_code,
                "content_type": fetched.media_type,
                "char_len": len(body),
            },
            metadata={
                "url": fetched.url,
                "status_code": fetched.status_code,
                "char_len": len(body),
                "truncated": len(body) == args.max_chars,
                "summary": f"fetched {fetched.url} ({fetched.status_code}, {len(body)} chars)",
            },
        )

    async def _fetch(
        self,
        url: str,
        *,
        allow_private: bool,
        timeout_seconds: float,
        max_chars: int,
    ) -> _Fetched:
        """GET ``url`` following guarded redirects by hand, streaming a capped body."""
        read_cap = max_chars * _READ_BYTE_MULTIPLIER + _READ_BYTE_FIXED
        current = url
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout_seconds,
            follow_redirects=False,  # redirects are followed by hand, guarded each hop
            headers={"user-agent": _USER_AGENT},
            trust_env=False,
        ) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                async with client.stream("GET", current) as response:
                    if response.status_code in _REDIRECT_CODES:
                        location = response.headers.get("location")
                        if not location:
                            return _fetched_for(response, current, b"")
                        next_url = urljoin(current, location)
                        try:
                            current = await asyncio.to_thread(
                                self._guard, next_url, allow_private=allow_private
                            )
                        except (NetworkGuardError, ValueError) as exc:
                            raise _RedirectRefused(str(exc)) from exc
                        continue
                    body = await _read_up_to(response, read_cap)
                    return _fetched_for(response, current, body)
        raise _TooManyRedirects(f"more than {_MAX_REDIRECTS} redirects from {url}")


def _fetched_for(response: httpx.Response, url: str, body: bytes) -> _Fetched:
    return _Fetched(
        url=url,
        status_code=response.status_code,
        media_type=_media_type(response.headers.get("content-type")),
        encoding=response.encoding or "utf-8",
        body=body,
    )


async def _read_up_to(response: httpx.Response, cap: int) -> bytes:
    """Read streamed bytes until ``cap``, then stop (bounds worker memory)."""
    parts: list[bytes] = []
    total = 0
    async for chunk in response.aiter_raw():
        room = cap - total
        if room <= 0:
            break
        parts.append(chunk[:room])
        total += min(len(chunk), room)
        if total >= cap:
            break
    return b"".join(parts)


def _media_type(content_type: str | None) -> str | None:
    """Lowercased media type with any ``; params`` stripped or ``None``."""
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower()


def _body_text(fetched: _Fetched) -> str:
    """Decode the body, stripping HTML tags for HTML media types."""
    text = fetched.body.decode(fetched.encoding or "utf-8", errors="replace")
    if fetched.media_type in _HTML_MEDIA_TYPES:
        return _html_to_text(text)
    return " ".join(text.split())


def _clamp(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _refused(exc: BaseException | str) -> ToolResult:
    message = str(exc)
    return tool_error(
        f"web_fetch refused: {message}.",
        root_cause=message,
        safe_retry="provide a public http(s) URL, or pass allow_private=True for local dev",
        stop_condition="stop fetching targets the guard refuses",
    )


def _resolve_hostname(raw: str | None) -> str | None:
    """Host of ``raw`` for the permission gate; ``None`` on a malformed URL (no crash)."""
    try:
        return urlparse(raw or "").hostname
    except ValueError:  # e.g. a malformed bracketed IPv6 host
        return None


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