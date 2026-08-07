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
from dream.utils.network_guard import NetworkGuardError, guard_web_url

_HTML_BODY = (
    "<html><head><script>var x=1;</script></head><body>"
    "<h1>Title</h1><p>Hello <b>world</b>.</p><p>Second para.</p></body></html>"
)


def _body_response(status: int, text: str, content_type: str = "text/plain") -> httpx.Response:
    """A response whose body is a *stream* so the tool can read it once via
    ``aiter_raw`` -- a plain ``Responses(text=...)`` is pre-consumed by httpx's
    ``MockTransport`` and would raise ``StreamConsumed`` on a streaming read."""
    return httpx.Response(
        status,
        headers={"content-type": content_type},
        stream=httpx.ByteStream(text.encode("utf-8")),
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
        return _body_response(200, _HTML_BODY, content_type="text/html")

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
        return _body_response(200, "hello plain body")

    result = await _tool(handler).execute(
        {"url": "http://example.com/plain"}, _ctx(tmp_path)
    )

    assert result.is_error is False
    assert result.content == "hello plain body"
    assert result.structured is not None
    assert result.structured["url"] == "http://example.com/plain"


async def test_content_type_with_params_is_html(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _body_response(200, _HTML_BODY, content_type="text/html; charset=utf-8")

    result = await _tool(handler).execute(
        {"url": "https://example.com/page"}, _ctx(tmp_path)
    )

    assert result.is_error is False
    assert "world" in result.content
    assert "<p>" not in result.content
    assert result.structured.get("content_type") == "text/html"


async def test_max_chars_clamps_output(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _body_response(200, "a" * 500)

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
        return _body_response(404, "not found")

    result = await _tool(handler).execute({"url": "https://example.com/missing"}, _ctx(tmp_path))

    assert result.is_error is True
    assert "404" in result.content


async def test_transport_error_is_structured(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await _tool(handler).execute({"url": "https://example.com/x"}, _ctx(tmp_path))

    assert result.is_error is True
    assert result.metadata.get("root_cause")


# --- redirects ---------------------------------------------------------------


async def test_redirects_are_followed_and_final_url_reported(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return _body_response(200, "final body")

    result = await _tool(handler).execute(
        {"url": "https://example.com/start"}, _ctx(tmp_path)
    )

    assert result.is_error is False
    assert result.content == "final body"
    assert result.structured.get("url") == "https://example.com/final"
    assert seen == ["https://example.com/start", "https://example.com/final"]


async def test_redirect_to_private_host_is_refused(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/internal"})

    def refuse_private(url: str, *, allow_private: bool = False) -> str:
        del allow_private
        if "127.0.0.1" in url or "localhost" in url:
            raise NetworkGuardError(f"refused: {url} resolves to 127.0.0.1")
        return url

    tool = WebFetchTool(
        transport=httpx.MockTransport(handler), guard=refuse_private
    )
    result = await tool.execute({"url": "https://example.com/start"}, _ctx(tmp_path))

    assert result.is_error is True
    assert "refused" in result.content


async def test_relative_redirect_location_is_resolved(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(307, headers={"location": "/final"})
        return _body_response(200, "ok")

    result = await _tool(handler).execute({"url": "https://example.com/start"}, _ctx(tmp_path))

    assert result.is_error is False
    assert result.structured.get("url") == "https://example.com/final"


async def test_redirect_loop_is_an_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/start"})

    result = await _tool(handler).execute({"url": "https://example.com/start"}, _ctx(tmp_path))

    assert result.is_error is True
    assert "redirect" in result.content.lower()


# --- malformed URLs ----------------------------------------------------------


async def test_malformed_ipv6_url_is_refused_not_crash(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _body_response(200, "unused")

    tool = WebFetchTool(
        transport=httpx.MockTransport(handler), guard=guard_web_url
    )
    result = await tool.execute({"url": "http://[::1"}, _ctx(tmp_path))

    assert result.is_error is True
    assert "refused" in result.content


def test_malformed_url_does_not_break_permission_gate() -> None:
    tool = WebFetchTool()
    assert tool.effects_for({"url": "http://[::1"}) == tool.effects_for({})