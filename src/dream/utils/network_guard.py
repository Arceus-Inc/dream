"""Domain allow/deny enforcement for web/network tools (SSRF guard).

``web_fetch`` accepts an arbitrary caller-supplied URL, so without a guard a
client -- or a model confused by injected page content -- could point the fetch
at private services: the host loopback, RFC1918 internals, link-local metadata
endpoints, or a public-looking hostname that resolves inward. That is
server-side request forgery (SSRF).

This module is the single fail-closed choke point used by such tools. It
refuses the request *before any bytes are exchanged* when:

- the scheme is not ``http`` / ``https``, or
- the host is missing, or
- the host (or any address it resolves to) is in non-global address space:

    - loopback, link-local, and unspecified addresses,
    - private RFC1918 (10/8, 172.16/12, 192.168/16) and IPv6 ULA (fc00/7),
    - IPv4 CGN (shared) ``100.64.0.0/10``,
    - multicast and reserved addresses.

The absence of this guard is the difference between ``web_search`` / ``web_extract``
(single trusted Tavily host, no guard) and ``web_fetch`` (arbitrary target, guard).

``allow_private`` is an explicit escape hatch for local development fixtures; it
turns the default from fail-closed to opt-in -- never fall-open for callers that
did not ask.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class NetworkGuardError(ValueError):
    """The URL is refused: non-http(s), hostless, or private/reserved address space."""


__all__ = ["NetworkGuardError", "guard_web_url"]

Address = ipaddress.IPv4Address | ipaddress.IPv6Address

# Explicitly-denied prefix netblocks. We rely on the address flags for the
# common cases (loopback, private, link-local), and add the nets those flags
# under-catch: IPv4 CGNAT (shared) is not what the stdlib defends as private,
# yet is operator-internal.
_BLOCKED_PREFIXES: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("100.64.0.0/10"),
)


def _resolve_host(host: str) -> list[Address]:
    """Resolve ``host`` to its addresses (v4 + v6), in a stub-friendly way.

    ``getaddrinfo`` is called without ``AI_ADDRCONFIG`` so a hosts that has only
    an IPv6 answer behind a v4-only stack is still seen and refused, rather than
    silently pass through the guard.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise NetworkGuardError(f"refused: could not resolve host {host!r}: {exc}") from exc

    seen: set[Address] = set()
    addresses: list[Address] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if address in seen:
            continue
        seen.add(address)
        addresses.append(address)

    if not addresses:
        raise NetworkGuardError(f"refused: host {host!r} resolved to no usable address")
    return addresses


def _is_non_global(address: Address) -> bool:
    """Return ``True`` for any address the guard refuses (non-global space)."""
    if address.is_multicast or address.is_unspecified:
        return True
    if address.is_loopback or address.is_link_local or address.is_reserved:
        return True
    if address.is_private:
        return True
    return any(address in net for net in _BLOCKED_PREFIXES)


def guard_web_url(url: str, *, allow_private: bool = False) -> str:
    """Validate an arbitrary ``http(s)://`` URL for outbound fetch (SSRF gate).

    Returns the URL unchanged when its host is global (and ``allow_private`` is
    false, the default). Raises :class:`NetworkGuardError` with a described
    reason otherwise. All enforcement happens here -- before transport has a
    chance to talk to the target.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:  # e.g. a malformed bracketed IPv6 host ("http://[::1")
        raise NetworkGuardError(f"refused: malformed URL: {exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise NetworkGuardError(
            f"refused: scheme {parsed.scheme!r} is not allowed; use http or https"
        )
    try:
        host = parsed.hostname
    except ValueError as exc:  # e.g. a malformed bracketed IPv6 host ("http://[::1")
        raise NetworkGuardError(f"refused: malformed URL host: {exc}") from exc
    if not host:
        raise NetworkGuardError(f"refused: URL has no host: {url!r}")

    resolution = _resolve_host(host)
    if allow_private:
        return url

    refused = tuple(str(address) for address in resolution if _is_non_global(address))
    if refused:
        raise NetworkGuardError(
            f"refused: {host!r} resolves to non-public address(es): {', '.join(refused)}"
        )
    return url