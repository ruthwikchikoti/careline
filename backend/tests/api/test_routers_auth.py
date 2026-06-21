"""Auth router tests (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_issue_token_returns_access_token(client: TestClient):
    response = client.post("/auth/token", json={"doctor_id": "dr-A"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"]
    assert payload["token_type"] == "bearer"


def test_token_round_trip_allows_authenticated_request(client: TestClient):
    token_response = client.post("/auth/token", json={"doctor_id": "dr-A"})
    token = token_response.json()["access_token"]
    response = client.get(
        "/patients/patient-A",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
