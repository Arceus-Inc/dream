"""Eval: web trio descriptions stay non-overlapping so the model can choose."""

from __future__ import annotations

from dream.tools.builtin.web_extract import WebExtractTool
from dream.tools.builtin.web_fetch import WebFetchTool
from dream.tools.builtin.web_search import WebSearchTool


def test_web_tool_descriptions_are_differentiated() -> None:
    search = WebSearchTool().description.lower()
    extract = WebExtractTool().description.lower()
    fetch = WebFetchTool().description.lower()

    assert "tavily" in search or "search" in search
    assert "url" in search or "urls" in search
    assert "fetch" not in search.split()  # find, don't fetch

    assert "extract" in extract or "cleaned" in extract
    assert "needs_render" in extract or "browser_run" in extract

    assert "ssrf" in fetch or "direct" in fetch
    assert "no api key" in fetch or "no key" in fetch
    assert "browser_run" in fetch

    # Pairwise: each description must mention its preferred sibling for handoff.
    assert "web_fetch" in search or "web_extract" in search
    assert "web_fetch" in extract
    assert "web_extract" in fetch
