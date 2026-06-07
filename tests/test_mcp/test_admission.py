"""Spec 06 MUST #12 — allowlist admission gate."""

from __future__ import annotations

from dream.mcp._admission import admit
from dream.mcp._types import AllowlistEntry
from dream.services.repo_validator import has_blocking


def _entry(name: str) -> AllowlistEntry:
    return AllowlistEntry(name=name, endpoint=f"stdio://{name}", transport="stdio")


def test_unlisted_configured_server_blocks() -> None:
    allowlist = [_entry("playwright")]
    admitted, findings = admit({"playwright", "experimental"}, allowlist)
    assert [e.name for e in admitted] == ["playwright"]
    assert has_blocking(findings)
    assert any("experimental" in f.message for f in findings)


def test_all_listed_yields_no_findings() -> None:
    allowlist = [_entry("playwright"), _entry("fs")]
    admitted, findings = admit({"playwright", "fs"}, allowlist)
    assert {e.name for e in admitted} == {"playwright", "fs"}
    assert findings == []


def test_empty_configured_admits_whole_allowlist() -> None:
    allowlist = [_entry("a"), _entry("b")]
    admitted, findings = admit(set(), allowlist)
    assert {e.name for e in admitted} == {"a", "b"}
    assert findings == []
