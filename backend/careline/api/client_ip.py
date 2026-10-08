"""Client-IP resolution for per-IP guards — spoof-resistant (REVIEW-5).

``X-Forwarded-For`` is a list the *client* starts: anything it sends arrives as
the leftmost entries, and each trusted proxy **appends** the peer it saw on the
right. Trusting the leftmost entry (what ``uvicorn --forwarded-allow-ips "*"``
effectively did) lets a caller rotate a fake IP per request and walk straight past
every per-IP limit.

So the key is taken from the right: with ``trusted_hops = N`` (the number of
proxies in front of the app that append to XFF — 1 on Render) the client IP is the
N-th entry from the right, i.e. the address the outermost trusted proxy observed.
With ``trusted_hops = 0`` (local dev, tests, direct exposure) XFF is ignored
entirely and the socket peer is used. If the header has fewer entries than
configured hops, we fall back to the socket peer — never to a client-supplied
value.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_UNKNOWN = "unknown"


def _forwarded_entries(headers: Sequence[tuple[bytes, bytes]]) -> list[str]:
    """All XFF entries in order, across repeated headers (RFC 7230 list join)."""
    entries: list[str] = []
    for name, value in headers:
        if name.lower() == b"x-forwarded-for":
            for part in value.decode("latin-1").split(","):
                part = part.strip()
                if part:
                    entries.append(part)
    return entries


def client_ip_from_scope(scope: Mapping[str, Any], *, trusted_hops: int) -> str:
    """Resolve the client IP for an ASGI scope (see module docstring)."""
    peer = (scope.get("client") or (_UNKNOWN, 0))[0] or _UNKNOWN
    if trusted_hops <= 0:
        return peer
    entries = _forwarded_entries(scope.get("headers") or [])
    if len(entries) < trusted_hops:
        return peer
    return entries[-trusted_hops]


__all__ = ["client_ip_from_scope"]
