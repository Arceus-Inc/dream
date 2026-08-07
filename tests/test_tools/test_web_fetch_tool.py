"""Default ``web_fetch`` tool -- direct, dependency-free HTTP fetch.

Unlike the Tavily-backed siblings this tool needs no API key and no CDP: it GETs
an arbitrary URL through the SSRF guard and returns text-only. The HTTP hop is
stubbed with :class:`httpx.MockTransport`; the DNS hop is stubbed by injecting a
guard that resolves every host as public.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.web_fetch import WebFetchTool
from dream.utils.network_guard import NetworkGuardError

_HTML_BODY = (
    "<html><head><script>var x=1;</script></head><body>"
    "<h1>Title</h1><p>Hello <b>world</b>.</p><p>Second para.</p></body></html>"
)


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_test", metadata={})


def _public_guard(url: str, *, allow_private: bool = False) -> str:
    """A guard that accepts every URL as public (so fetch tests skip DNS)."""
    del allow_private
    if not url.startswith(("http://", "https://")):
        raise NetworkGuardError("refused: scheme not allowed")
    return url


def _tool(handler, *, guard=None) -> WebFetchTool:
    return WebFetchTool(
        transport=httpx.MockTransport(handler), guard=guard or _public_guard
    )


# --- declaration -----------------------------------------------------------


def test_web_fetch_is_external_network_tier() -> None:
    tool = WebFetchTool()
    assert tool.name == "web_fetch"
    assert tool.declaration.risk == "external"
    assert tool.declaration.tier_required == 2
    assert tool.is_read_only() is False


def test_web_fetch_reports_the_target_host() -> None:
    effects = WebFetchTool().effects_for({"url": "https://example.com/x"})
    assert effects.network_host == "example.com"


# --- happy path ---------------------------------------------------------------


async def test_fetch_returns_text_only(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"]
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=_HTML_BODY,
        )

    result = await _tool(handler).execute(
        {"url": "https://example.com/page"}, _ctx(tmp_path)
    )

    assert result.is_error is False
    assert "Title" in result.content
    assert "world" in result.content
    assert "<script>" not in result.content
    assert result.metadata.get("char_len") > 0


async def test_web_fetch_exposes_structured_metadata(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, text="hello plain body"
        )

    result = await _tool(handler).execute(
        {"url": "http://example.com/plain"}, _ctx(tmp_path)
    )

    assert result.is_error is False
    assert result.content == "hello plain body"
    assert result.structured is not None
    assert result.structured["url"] == "http://example.com/plain"


async def test_max_chars_clamps_output(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="a" * 500)

    result = await _tool(handler).execute(
        {"url": "https://example.com/x", "max_chars": 50}, _ctx(tmp_path)
    )

    assert len(result.content) <= 50
    assert result.metadata.get("truncated") is True


# --- guard + failure modes ------------------------------------------------------


async def test_private_target_is_refused_before_fetch(tmp_path: Path) -> None:
    def refuse(url: str, *, allow_private: bool = False) -> str:
        del allow_private
        raise NetworkGuardError(f"refused: {url} resolves to 127.0.0.1")

    tool = WebFetchTool(transport=httpx.MockTransport(lambda r: httpx.Response(200)), guard=refuse)
    result = await tool.execute({"url": "http://localhost:8000/x"}, _ctx(tmp_path))

    assert result.is_error is True
    assert "refused" in result.content
    assert result.metadata.get("root_cause")


async def test_4xx_is_structured_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    result = await _tool(handler).execute({"url": "https://example.com/missing"}, _ctx(tmp_path))

    assert result.is_error is True
    assert "404" in result.content


async def test_transport_error_is_structured(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await _tool(handler).execute({"url": "https://example.com/x"}, _ctx(tmp_path))

    assert result.is_error is True
    assert result.metadata.get("root_cause")