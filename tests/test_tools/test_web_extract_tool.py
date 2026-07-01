"""Default ``web_extract`` tool — Tavily-backed page fetch + clean extraction.

Sibling of ``web_search`` (tier 2, ``risk="external"``): it reaches Tavily's
``/extract`` endpoint, reports ``network_host``, and is not read-only. The API
key rides the environment; a missing key degrades to the Spec 05 three-part
error contract. Each result carries a ``needs_render`` signal the research agent
branches on to decide whether a page is a JS shell that warrants a browser
fallback. The HTTP hop is stubbed with :class:`httpx.MockTransport`.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.web_extract import WebExtractTool

_ARTICLE = "Spain defeated England 2-1 in the Euro 2024 final in Berlin. " * 20  # >200 chars
_TAVILY_OK = {
    "results": [
        {"url": "https://example.com/euro-2024-final", "raw_content": _ARTICLE},
    ],
    "failed_results": [],
    "response_time": 0.42,
}


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_test", metadata={})


def _tool(handler) -> WebExtractTool:
    """Build a tool whose HTTP hop is served by ``handler`` with a test key."""
    return WebExtractTool(api_key="test-key", transport=httpx.MockTransport(handler))


# --- declaration -----------------------------------------------------------


def test_web_extract_is_external_network_tier() -> None:
    tool = WebExtractTool()
    assert tool.name == "web_extract"
    assert tool.declaration.risk == "external"
    assert tool.declaration.tier_required == 2
    assert tool.is_read_only() is False


def test_web_extract_reports_network_host() -> None:
    effects = WebExtractTool().effects_for({"urls": ["https://x.com"]})
    assert effects.network_host == "api.tavily.com"


# --- happy path ------------------------------------------------------------


async def test_web_extract_returns_clean_content(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["api_key"] == "test-key"
        assert body["urls"] == ["https://example.com/euro-2024-final"]
        assert body["extract_depth"] == "basic"
        return httpx.Response(200, json=_TAVILY_OK)

    result = await _tool(handler).execute(
        {"urls": ["https://example.com/euro-2024-final"]}, _ctx(tmp_path)
    )

    assert result.is_error is False
    assert "Spain defeated England 2-1" in result.content
    structured = result.structured
    assert structured is not None
    entry = structured["results"][0]
    assert entry["extraction_ok"] is True
    assert entry["needs_render"] is False
    assert structured["any_needs_render"] is False
    assert result.metadata.get("extracted") == 1


# --- the escalation signal (JS-shell detection) ----------------------------


async def test_thin_content_flags_needs_render(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"results": [{"url": "https://spa.app", "raw_content": "Loading…"}]}
        )

    result = await _tool(handler).execute({"urls": ["https://spa.app"]}, _ctx(tmp_path))

    structured = result.structured
    assert structured is not None
    assert structured["results"][0]["needs_render"] is True
    assert structured["any_needs_render"] is True


async def test_js_placeholder_flags_needs_render(tmp_path: Path) -> None:
    shell = "You need to enable JavaScript to run this app. " * 6  # long but a JS shell
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"results": [{"url": "https://spa.app", "raw_content": shell}]}
        )

    result = await _tool(handler).execute({"urls": ["https://spa.app"]}, _ctx(tmp_path))

    structured = result.structured
    assert structured is not None
    assert structured["results"][0]["needs_render"] is True


async def test_failed_url_flags_needs_render(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [],
                "failed_results": [{"url": "https://blocked.com", "error": "timeout"}],
            },
        )

    result = await _tool(handler).execute({"urls": ["https://blocked.com"]}, _ctx(tmp_path))

    structured = result.structured
    assert structured is not None
    entry = structured["results"][0]
    assert entry["extraction_ok"] is False
    assert entry["needs_render"] is True
    assert entry["error"] == "timeout"


# --- failure modes ---------------------------------------------------------


async def test_web_extract_missing_key_is_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DREAM_TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = await WebExtractTool().execute({"urls": ["https://x.com"]}, _ctx(tmp_path))

    assert result.is_error is True
    assert result.metadata.get("root_cause")
    assert result.metadata.get("safe_retry")
    assert result.metadata.get("stop_condition")


async def test_web_extract_env_key_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_TAVILY_API_KEY", "env-key")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    tool = WebExtractTool(transport=httpx.MockTransport(handler))
    await tool.execute({"urls": ["https://x.com"]}, _ctx(tmp_path))

    assert seen["api_key"] == "env-key"


async def test_web_extract_bad_key_reports_401(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Unauthorized"})

    result = await _tool(handler).execute({"urls": ["https://x.com"]}, _ctx(tmp_path))

    assert result.is_error is True
    assert "401" in result.content


async def test_web_extract_rate_limit_reports_429(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "Too Many Requests"})

    result = await _tool(handler).execute({"urls": ["https://x.com"]}, _ctx(tmp_path))

    assert result.is_error is True
    assert "429" in result.content


async def test_web_extract_transport_error_is_structured(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await _tool(handler).execute({"urls": ["https://x.com"]}, _ctx(tmp_path))

    assert result.is_error is True
    assert result.metadata.get("root_cause")
