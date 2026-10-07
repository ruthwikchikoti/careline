"""Budget guard — rate limit + daily cap on spend-bearing POSTs."""

from __future__ import annotations

from fastapi.testclient import TestClient

from careline.api.app import create_app
from careline.config import Settings


def _app(limits: dict):
    return create_app(settings=Settings(**limits))


def test_health_and_meta_endpoints_exist():
    with TestClient(_app({})) as client:
        assert client.get("/health").json() == {"status": "ok"}
        meta = client.get("/api/meta").json()
        assert meta["fictional_data"] is True
        assert "emergency" in meta["disclaimer"].lower()


def test_rate_limit_rejects_after_window():
    with TestClient(_app({"rate_limit_per_minute": 2})) as client:
        # Non-spend routes are never limited.
        for _ in range(5):
            assert client.get("/health").status_code == 200
        # Spend route: first two pass the guard (any router status is fine —
        # the guard is what we test), the third is a 429 from the middleware.
        statuses = [
            client.post("/demo/ask", json={"question": "q"}).status_code for _ in range(3)
        ]
        assert statuses[:2] != [429, 429]
        assert statuses[2] == 429


def test_daily_cap_returns_503():
    with TestClient(_app({"daily_request_cap": 1})) as client:
        first = client.post("/demo/ask", json={"question": "q"}).status_code
        second = client.post("/demo/ask", json={"question": "q"}).status_code
        assert first != 503
        assert second == 503
        body = client.post("/demo/ask", json={"question": "q"}).json()
        assert "budget" in body["detail"].lower()


def test_guards_off_by_default():
    with TestClient(_app({})) as client:
        statuses = {
            client.post("/demo/ask", json={"question": "q"}).status_code for _ in range(10)
        }
        assert 429 not in statuses and 503 not in statuses


def test_rejections_carry_cors_headers():
    """A browser console must be able to READ the 429 body (middleware order)."""
    app = create_app(
        settings=Settings(
            rate_limit_per_minute=1, allowed_origins="https://console.example"
        )
    )
    with TestClient(app) as client:
        client.post("/demo/ask", json={"question": "q"})  # passes the guard
        rejected = client.post(
            "/demo/ask",
            json={"question": "q"},
            headers={"Origin": "https://console.example"},
        )
        assert rejected.status_code == 429
        assert (
            rejected.headers.get("access-control-allow-origin") == "https://console.example"
        ), "BudgetGuard rejection bypassed CORS — outer browser callers cannot read the body"


def test_daily_cap_counts_only_accepted_requests():
    """Rejected/invalid POSTs spend nothing and must not burn the daily cap."""
    with TestClient(_app({"daily_request_cap": 2})) as client:
        # Two requests the router answers 422 (missing body) — no spend.
        for _ in range(4):
            client.post("/demo/ask")
        # Cap not burned: a valid request still goes through.
        ok = client.post("/demo/ask", json={"question": "q"})
        assert ok.status_code != 503
