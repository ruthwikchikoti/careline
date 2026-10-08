"""Trust X-Forwarded-Proto for the request *scheme* only (SECURITY-2).

The container runs uvicorn with ``--no-proxy-headers`` so a client-controlled
X-Forwarded-For can never rewrite the client address (REVIEW-5; per-IP guards
resolve the IP themselves via :mod:`careline.api.client_ip`). The side effect is
that uvicorn also stops honouring X-Forwarded-Proto: behind Render's
TLS-terminating proxy the ASGI scheme is ``http``, so every redirect the app
generates (FastAPI's trailing-slash 307) pointed at ``http://`` — a downgrade the
browser blocks as mixed content.

This middleware restores **only** the scheme, and only when a trusted proxy is
configured (``CARELINE_TRUSTED_PROXY_HOPS > 0``). It takes the N-th entry from the
right of X-Forwarded-Proto — the value the outermost trusted proxy set — and
accepts nothing but ``http``/``https`` (``ws``/``wss`` for websockets). The client
address is never touched. Worst case, a spoofed value only changes the scheme of
a redirect sent back to the spoofing client itself.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

_ALLOWED = {
    "http": {"http", "https"},
    "websocket": {"ws", "wss"},
}
_HTTP_TO_WS = {"http": "ws", "https": "wss"}


def _forwarded_protos(headers) -> list[str]:
    entries: list[str] = []
    for name, value in headers:
        if name.lower() == b"x-forwarded-proto":
            for part in value.decode("latin-1").split(","):
                part = part.strip().lower()
                if part:
                    entries.append(part)
    return entries


class ForwardedProtoMiddleware:
    """Pure-ASGI middleware: set ``scope["scheme"]`` from a trusted X-Forwarded-Proto."""

    def __init__(self, app, *, trusted_proxy_hops: int = 0) -> None:
        self.app = app
        self.trusted_proxy_hops = trusted_proxy_hops

    async def __call__(self, scope, receive, send) -> None:
        kind = scope.get("type")
        if self.trusted_proxy_hops > 0 and kind in _ALLOWED:
            entries = _forwarded_protos(scope.get("headers") or [])
            if len(entries) >= self.trusted_proxy_hops:
                proto = entries[-self.trusted_proxy_hops]
                if kind == "websocket":
                    proto = _HTTP_TO_WS.get(proto, proto)
                if proto in _ALLOWED[kind]:
                    scope = {**scope, "scheme": proto}
        await self.app(scope, receive, send)


__all__ = ["ForwardedProtoMiddleware"]
