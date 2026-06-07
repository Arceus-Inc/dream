"""Spec 06 slice 4 — the production opener dispatches stdio/http/ws.

We don't stand up real servers here; the transport client factories are faked so
we assert the opener picks the right transport and merges credentials. Manager
behaviour over a *real* ClientSession stays covered by the in-memory tests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from dream.mcp import _openers
from dream.mcp._credentials import ServerCredential, write_credential
from dream.mcp._openers import UnsupportedTransportError, make_default_opener
from dream.mcp._types import AllowlistEntry, McpTransport


class _FakeSession:
    def __init__(self, read: object, write: object) -> None:
        self.read = read
        self.write = write

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
def record(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    @asynccontextmanager
    async def fake_stdio(server: Any, **kw: Any) -> Any:
        calls["stdio"] = server
        yield ("r-stdio", "w-stdio")

    @asynccontextmanager
    async def fake_http(url: str, headers: Any = None, **kw: Any) -> Any:
        calls["http"] = {"url": url, "headers": headers}
        yield ("r-http", "w-http", lambda: None)

    @asynccontextmanager
    async def fake_ws(url: str) -> Any:
        calls["ws"] = {"url": url}
        yield ("r-ws", "w-ws")

    monkeypatch.setattr(_openers, "stdio_client", fake_stdio)
    monkeypatch.setattr(_openers, "streamablehttp_client", fake_http)
    monkeypatch.setattr(_openers, "websocket_client", fake_ws)
    monkeypatch.setattr(_openers, "ClientSession", _FakeSession)
    return calls


def _entry(name: str, transport: McpTransport, endpoint: str) -> AllowlistEntry:
    return AllowlistEntry(name=name, endpoint=endpoint, transport=transport)


async def test_stdio_dispatch_builds_params(record: dict[str, Any]) -> None:
    opener = make_default_opener(None)
    async with opener(_entry("pw", "stdio", "stdio://run-server --flag")) as session:
        assert isinstance(session, _FakeSession)
        assert session.read == "r-stdio"
    params = record["stdio"]
    assert params.command == "run-server"
    assert params.args == ["--flag"]


async def test_http_dispatch_passes_url(record: dict[str, Any]) -> None:
    opener = make_default_opener(None)
    async with opener(_entry("api", "http", "https://example/mcp")) as session:
        assert session.read == "r-http"
    assert record["http"]["url"] == "https://example/mcp"


async def test_ws_dispatch_passes_url(record: dict[str, Any]) -> None:
    opener = make_default_opener(None)
    async with opener(_entry("rt", "ws", "wss://example/mcp")) as session:
        assert session.read == "r-ws"
    assert record["ws"]["url"] == "wss://example/mcp"


async def test_http_merges_credentials_into_headers(
    record: dict[str, Any], tmp_path: Path
) -> None:
    creds = tmp_path / "creds.toml"
    write_credential(creds, "api", ServerCredential(mode="bearer", value="tok"))
    opener = make_default_opener(creds)
    async with opener(_entry("api", "http", "https://example/mcp")):
        pass
    assert record["http"]["headers"] == {"Authorization": "Bearer tok"}


async def test_no_credentials_path_means_no_headers(record: dict[str, Any]) -> None:
    opener = make_default_opener(None)
    async with opener(_entry("api", "http", "https://example/mcp")):
        pass
    assert record["http"]["headers"] is None


async def test_ws_unavailable_raises_unsupported(
    record: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_openers, "websocket_client", None)
    opener = make_default_opener(None)
    with pytest.raises(UnsupportedTransportError):
        async with opener(_entry("rt", "ws", "wss://example/mcp")):
            pass
