"""Default ``web_search`` tool — Tavily-backed external search.

External (tier 2, ``risk="external"``): the tool reaches Tavily's API, so it
reports ``network_host`` and is not read-only. The API key rides the
environment, not the model-provider auth map; a missing key degrades to the
Spec 05 three-part error contract rather than crashing. The HTTP hop is stubbed
with :class:`httpx.MockTransport` so these tests never touch the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.web_search import WebSearchTool

_TAVILY_OK = {
    "query": "who won euro 2024",
    "answer": "Spain won UEFA Euro 2024, beating England 2-1.",
    "results": [
        {
            "title": "UEFA Euro 2024 Final",
            "url": "https://example.com/euro-2024-final",
            "content": "Spain defeated England 2-1 in the final in Berlin.",
            "score": 0.98,
        },
        {
            "title": "Euro 2024 recap",
            "url": "https://example.com/recap",
            "content": "A tournament recap and player ratings.",
            "score": 0.81,
        },
    ],
}


def _ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_test", metadata={})


def _tool(handler) -> WebSearchTool:
    """Build a tool whose HTTP hop is served by ``handler`` with a test key."""
    return WebSearchTool(api_key="test-key", transport=httpx.MockTransport(handler))


# --- declaration -----------------------------------------------------------


def test_web_search_is_external_network_tier() -> None:
    tool = WebSearchTool()
    assert tool.name == "web_search"
    assert tool.declaration.risk == "external"
    assert tool.declaration.tier_required == 2
    assert tool.is_read_only() is False


def test_web_search_reports_network_host() -> None:
    effects = WebSearchTool().effects_for({"query": "x"})
    assert effects.network_host == "api.tavily.com"


# --- happy path ------------------------------------------------------------


async def test_web_search_returns_rendered_results(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["api_key"] == "test-key"
        assert body["query"] == "who won euro 2024"
        assert body["max_results"] == 2
        return httpx.Response(200, json=_TAVILY_OK)

    result = await _tool(handler).execute(
        {"query": "who won euro 2024", "max_results": 2}, _ctx(tmp_path)
    )

    assert result.is_error is False
    assert "Spain won UEFA Euro 2024" in result.content
    assert "https://example.com/euro-2024-final" in result.content
    assert result.metadata.get("result_count") == 2
    assert result.structured is not None
    assert len(result.structured["results"]) == 2


async def test_web_search_forwards_domain_filters(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    await _tool(handler).execute(
        {
            "query": "papers",
            "include_domains": ["arxiv.org"],
            "exclude_domains": ["example.com"],
        },
        _ctx(tmp_path),
    )

    assert seen["include_domains"] == ["arxiv.org"]
    assert seen["exclude_domains"] == ["example.com"]


async def test_web_search_empty_results_is_graceful(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [], "answer": ""})

    result = await _tool(handler).execute({"query": "zzzznothing"}, _ctx(tmp_path))

    assert result.is_error is False
    assert result.metadata.get("result_count") == 0
    assert "no results" in result.content.lower()


# --- failure modes ---------------------------------------------------------


async def test_web_search_missing_key_is_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DREAM_TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = await WebSearchTool().execute({"query": "x"}, _ctx(tmp_path))

    assert result.is_error is True
    assert result.metadata.get("root_cause")
    assert result.metadata.get("safe_retry")
    assert result.metadata.get("stop_condition")


async def test_web_search_env_key_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_TAVILY_API_KEY", "env-key")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    tool = WebSearchTool(transport=httpx.MockTransport(handler))
    await tool.execute({"query": "x"}, _ctx(tmp_path))

    assert seen["api_key"] == "env-key"


async def test_web_search_bad_key_reports_401(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Unauthorized"})

    result = await _tool(handler).execute({"query": "x"}, _ctx(tmp_path))

    assert result.is_error is True
    assert "401" in result.content


async def test_web_search_rate_limit_reports_429(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "Too Many Requests"})

    result = await _tool(handler).execute({"query": "x"}, _ctx(tmp_path))

    assert result.is_error is True
    assert "429" in result.content


async def test_web_search_transport_error_is_structured(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await _tool(handler).execute({"query": "x"}, _ctx(tmp_path))

    assert result.is_error is True
    assert result.metadata.get("root_cause")
