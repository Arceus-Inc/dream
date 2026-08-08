"""Eval: web_search / web_fetch descriptions stay non-overlapping."""

from __future__ import annotations

from dream.tools.builtin.web_fetch import WebFetchTool
from dream.tools.builtin.web_search import WebSearchTool


def test_web_tool_descriptions_are_differentiated() -> None:
    search = WebSearchTool().description.lower()
    fetch = WebFetchTool().description.lower()

    assert "tavily" in search or "search" in search
    assert "url" in search or "urls" in search
    assert "fetch" not in search.split()  # find, don't fetch

    assert "ssrf" in fetch or "direct" in fetch
    assert "no api key" in fetch or "no key" in fetch
    assert "browser_run" in fetch

    assert "web_fetch" in search
    assert "web_extract" not in search
    assert "web_extract" not in fetch
