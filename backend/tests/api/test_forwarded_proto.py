"""Redirects keep https behind Render's TLS-terminating proxy (SECURITY-2).

The Dockerfile runs uvicorn with ``--no-proxy-headers`` (so a spoofed
X-Forwarded-For can't rewrite the client IP — REVIEW-5). Side effect: the ASGI
scope scheme is ``http`` behind Render's https proxy, so any redirect the app
generates (e.g. the trailing-slash redirect) pointed at ``http://`` — a downgrade
the browser blocks as mixed content. :class:`ForwardedProtoMiddleware` trusts
X-Forwarded-Proto for the **scheme only** (never the client address), and only
when a trusted proxy is configured.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from careline.api.proxy_scheme import ForwardedProtoMiddleware


def _app(trusted_hops: int) -> FastAPI:
    app = FastAPI()

    @app.get("/items")
    async def items() -> dict:
        return {"ok": True}

    @app.get("/scope")
    async def scope(request: Request) -> dict:
        return {"scheme": request.url.scheme, "client": request.client.host}

    app.add_middleware(ForwardedProtoMiddleware, trusted_proxy_hops=trusted_hops)
    return app


def test_trailing_slash_redirect_keeps_https_behind_a_trusted_proxy():
    with TestClient(_app(1), base_url="http://careline.example") as client:
        r = client.get(
            "/items/", headers={"X-Forwarded-Proto": "https"}, follow_redirects=False
        )
    assert r.status_code in (307, 308)
    assert r.headers["location"].startswith("https://careline.example/items")


def test_scheme_only_client_address_is_untouched():
    with TestClient(_app(1)) as client:
        body = client.get(
            "/scope",
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "6.6.6.6"},
        ).json()
    assert body["scheme"] == "https"
    assert body["client"] != "6.6.6.6"


def test_forwarded_proto_ignored_without_a_trusted_proxy():
    with TestClient(_app(0)) as client:
        body = client.get("/scope", headers={"X-Forwarded-Proto": "https"}).json()
    assert body["scheme"] == "http"


def test_garbage_forwarded_proto_is_ignored():
    with TestClient(_app(1)) as client:
        body = client.get("/scope", headers={"X-Forwarded-Proto": "javascript"}).json()
    assert body["scheme"] == "http"


def test_rightmost_trusted_proto_wins_over_client_supplied_left_entries():
    with TestClient(_app(1)) as client:
        body = client.get("/scope", headers={"X-Forwarded-Proto": "http, https"}).json()
    assert body["scheme"] == "https"


def test_dockerfile_keeps_no_proxy_headers():
    from pathlib import Path

    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    cmd = dockerfile[dockerfile.index("CMD") :]
    assert "--no-proxy-headers" in cmd
