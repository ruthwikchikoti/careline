"""FastAPI app smoke + error-handler tests (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_app_starts(client: TestClient):
    response = client.get("/openapi.json")
    assert response.status_code == 200


def test_unknown_route_returns_404(client: TestClient):
    response = client.get("/does-not-exist")
    assert response.status_code == 404


def test_bad_bearer_token_returns_401(client: TestClient):
    response = client.get(
        "/patients/patient-A",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_error_responses_do_not_leak_tracebacks(client: TestClient):
    response = client.get(
        "/patients/patient-A",
        headers={"Authorization": "Bearer bad"},
    )
    body = response.text
    assert "Traceback" not in body
    assert "File \"" not in body
