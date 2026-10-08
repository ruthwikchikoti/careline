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


# --- REVIEW-5: patient routes guarded; X-Forwarded-For cannot be spoofed ------


def test_patient_ask_and_login_are_rate_limited():
    with TestClient(_app({"rate_limit_per_minute": 2})) as client:
        for path, body in (
            ("/patient/login", {"doctor_id": "d", "patient_id": "p", "pin": "1"}),
            ("/patient/ask", {"question": "q"}),
        ):
            statuses = [client.post(path, json=body).status_code for _ in range(3)]
            assert statuses[2] == 429, (path, statuses)


def test_doctor_login_is_rate_limited():
    with TestClient(_app({"rate_limit_per_minute": 2})) as client:
        body = {"doctor_id": "dr-A", "password": "x"}
        statuses = [client.post("/auth/token", json=body).status_code for _ in range(3)]
        assert statuses[2] == 429


def test_login_routes_do_not_burn_the_daily_spend_cap():
    """Logins are rate-limited per IP but are not LLM spend."""
    with TestClient(_app({"daily_request_cap": 1})) as client:
        for _ in range(3):
            client.post("/auth/token", json={"doctor_id": "dr-A", "password": "x"})
        assert client.post("/demo/ask", json={"question": "q"}).status_code != 503


def test_rotating_spoofed_xff_does_not_bypass_the_limit():
    """The client controls the LEFT of X-Forwarded-For; the trusted proxy appends
    the real peer on the RIGHT. Keying on the rightmost hop makes rotation useless."""
    with TestClient(_app({"rate_limit_per_minute": 2, "trusted_proxy_hops": 1})) as client:
        statuses = [
            client.post(
                "/demo/ask",
                json={"question": "q"},
                headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
            ).status_code
            for i in range(4)
        ]
        assert statuses[2] == 429 and statuses[3] == 429


def test_rightmost_hop_still_separates_real_clients():
    with TestClient(_app({"rate_limit_per_minute": 1, "trusted_proxy_hops": 1})) as client:
        a = client.post(
            "/demo/ask", json={"question": "q"}, headers={"X-Forwarded-For": "203.0.113.1"}
        )
        b = client.post(
            "/demo/ask", json={"question": "q"}, headers={"X-Forwarded-For": "203.0.113.2"}
        )
        assert a.status_code != 429 and b.status_code != 429


def test_xff_is_ignored_without_a_trusted_proxy():
    """Default (no trusted hops): the socket peer is the key; XFF is attacker data."""
    with TestClient(_app({"rate_limit_per_minute": 2})) as client:
        statuses = [
            client.post(
                "/demo/ask", json={"question": "q"}, headers={"X-Forwarded-For": f"10.0.0.{i}"}
            ).status_code
            for i in range(3)
        ]
        assert statuses[2] == 429


def test_client_ip_helper_takes_the_trusted_hop():
    from careline.api.client_ip import client_ip_from_scope

    def scope(xff: str | None) -> dict:
        headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
        return {"type": "http", "client": ("10.9.9.9", 1234), "headers": headers}

    assert client_ip_from_scope(scope("1.1.1.1, 2.2.2.2"), trusted_hops=0) == "10.9.9.9"
    assert client_ip_from_scope(scope("1.1.1.1, 2.2.2.2"), trusted_hops=1) == "2.2.2.2"
    assert client_ip_from_scope(scope("1.1.1.1, 2.2.2.2"), trusted_hops=2) == "1.1.1.1"
    # Fewer hops than configured → fall back to the socket peer (never the left).
    assert client_ip_from_scope(scope("2.2.2.2"), trusted_hops=2) == "10.9.9.9"
    assert client_ip_from_scope(scope(None), trusted_hops=1) == "10.9.9.9"


def test_dockerfile_does_not_trust_every_forwarded_ip():
    from pathlib import Path

    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    cmd = dockerfile[dockerfile.index("CMD") :]
    assert '"--forwarded-allow-ips", "*"' not in cmd
    assert "--no-proxy-headers" in cmd
