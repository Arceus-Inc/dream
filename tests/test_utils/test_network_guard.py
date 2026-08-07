"""``guard_web_url`` -- SSRF enforcement for arbitrary-URL network tools.

The guard must refuse non-http(s) schemes, hostless URLs, and any target that
resolves to non-global address space -- before transport runs. Resolution is
stubbed via ``_resolve_host`` so the tests never touch DNS.
"""

from __future__ import annotations

import ipaddress

import pytest

from dream.utils import network_guard
from dream.utils.network_guard import NetworkGuardError, guard_web_url

_DREAM = "dream.utils.network_guard"


def _resolve(monkeypatch: pytest.MonkeyPatch, host: str, addresses: tuple[str, ...]) -> None:
    def fake(expected: str) -> list[ipaddress._BaseAddress]:
        assert expected == host
        return [ipaddress.ip_address(address) for address in addresses]

    monkeypatch.setattr(network_guard, "_resolve_host", fake)


def test_public_url_passes_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve(monkeypatch, "example.com", ("93.184.216.34",))
    assert guard_web_url("https://example.com/page") == "https://example.com/page"


def test_private_rfc1918_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve(monkeypatch, "internal.app", ("10.0.0.5",))
    with pytest.raises(NetworkGuardError, match="non-public"):
        guard_web_url("https://internal.app/x")


def test_loopback_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve(monkeypatch, "localhost", ("127.0.0.1",))
    with pytest.raises(NetworkGuardError, match=r"127\.0\.0\.1"):
        guard_web_url("http://localhost:8000")


def test_link_local_metadata_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve(monkeypatch, "169.254.169.254", ("169.254.169.254",))
    with pytest.raises(NetworkGuardError, match="non-public"):
        guard_web_url("http://169.254.169.254/latest/meta-data")


def test_cgn_shared_space_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # 100.64.0.0/10 is CGNAT (shared operator space) -- not flagged private by stdlib.
    _resolve(monkeypatch, "100.64.42.1", ("100.64.42.1",))
    with pytest.raises(NetworkGuardError, match="non-public"):
        guard_web_url("http://100.64.42.1")


def test_ipv6_ula_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve(monkeypatch, "fd00::1", ("fd00::1",))
    with pytest.raises(NetworkGuardError, match="non-public"):
        guard_web_url("https://[fd00::1]")


def test_any_private_among_public_answers_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve(monkeypatch, "two.example", ("8.8.8.8", "172.16.0.9"))
    with pytest.raises(NetworkGuardError, match=r"172\.16\.0\.9"):
        guard_web_url("https://two.example")


def test_non_http_scheme_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve(monkeypatch, "example.com", ("93.184.216.34",))
    for scheme in ("ftp", "file", "gopher", "javascript"):
        with pytest.raises(NetworkGuardError, match="scheme"):
            guard_web_url(f"{scheme}://example.com")


def test_hostless_url_is_refused() -> None:
    with pytest.raises(NetworkGuardError, match="no host"):
        guard_web_url("https:///path")


def test_malformed_bracketed_ipv6_is_refused_not_crash() -> None:
    # urlparse('http://[::1') raises ValueError ("Invalid IPv6 URL"); the guard
    # must surface that as a NetworkGuardError rather than crash.
    with pytest.raises(NetworkGuardError, match="malformed"):
        guard_web_url("http://[::1")


def test_unresolvable_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(host: str) -> list[ipaddress._BaseAddress]:
        assert host == "nowhere.invalid"
        raise NetworkGuardError(f"refused: could not resolve host {host!r}")

    monkeypatch.setattr(network_guard, "_resolve_host", fail)
    with pytest.raises(NetworkGuardError, match="resolve"):
        guard_web_url("https://nowhere.invalid")


def test_allow_private_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve(monkeypatch, "localhost", ("127.0.0.1",))
    assert guard_web_url("http://localhost:8080", allow_private=True) == "http://localhost:8080"