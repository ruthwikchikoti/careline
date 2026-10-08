"""Auth router tests (NR-6, REVIEW-2/3).

Doctor tokens are minted only against the per-deployment doctor credential
(``CARELINE_DOCTOR_PASSWORD``); a bare ``doctor_id`` is never enough. The conftest
sets a known test credential so the suite stays keyless.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.conftest import TEST_DOCTOR_PASSWORD


def test_issue_token_returns_access_token(client: TestClient):
    response = client.post(
        "/auth/token", json={"doctor_id": "dr-A", "password": TEST_DOCTOR_PASSWORD}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"]
    assert payload["token_type"] == "bearer"


def test_token_round_trip_allows_authenticated_request(client: TestClient):
    token_response = client.post(
        "/auth/token", json={"doctor_id": "dr-A", "password": TEST_DOCTOR_PASSWORD}
    )
    token = token_response.json()["access_token"]
    response = client.get(
        "/patients/patient-A",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}


# --- REVIEW-2: no credential, no token ---------------------------------------


def test_token_without_credential_is_rejected(client: TestClient):
    """The old contract (doctor_id only) must no longer mint a token."""
    response = client.post("/auth/token", json={"doctor_id": "dr-A"})
    assert response.status_code == 422
    assert "access_token" not in response.json()


def test_token_with_wrong_credential_is_401(client: TestClient):
    response = client.post(
        "/auth/token", json={"doctor_id": "dr-A", "password": "not-the-password"}
    )
    assert response.status_code == 401
    assert "access_token" not in response.json()


def test_token_with_empty_credential_is_rejected(client: TestClient):
    response = client.post("/auth/token", json={"doctor_id": "dr-A", "password": ""})
    assert response.status_code in (401, 422)
    assert "access_token" not in response.json()


def test_reserved_demo_doctor_cannot_obtain_a_token(client: TestClient):
    """``demo-doctor`` owns the anonymous console turns — nobody may sign in as it."""
    response = client.post(
        "/auth/token",
        json={"doctor_id": "demo-doctor", "password": TEST_DOCTOR_PASSWORD},
    )
    assert response.status_code == 401


def test_doctor_allowlist_restricts_which_ids_can_sign_in(monkeypatch):
    from careline.api.app import create_app

    monkeypatch.setenv("CARELINE_DOCTOR_IDS", "dr-asha, dr-ravi")
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        ok = client.post(
            "/auth/token", json={"doctor_id": "dr-asha", "password": TEST_DOCTOR_PASSWORD}
        )
        assert ok.status_code == 200
        denied = client.post(
            "/auth/token", json={"doctor_id": "dr-evil", "password": TEST_DOCTOR_PASSWORD}
        )
        assert denied.status_code == 401


# --- REVIEW-3: role confusion -------------------------------------------------


def test_patient_token_is_not_accepted_as_a_doctor_token(client: TestClient):
    """A patient session must never authenticate a doctor route."""
    patient_token = client.app.state.auth_svc.issue_patient_token(
        patient_id="dr-A", doctor_id="dr-A"
    )
    response = client.get("/patients", headers={"Authorization": f"Bearer {patient_token}"})
    assert response.status_code == 401


def test_doctor_token_is_not_accepted_on_the_patient_portal(client: TestClient):
    login = client.post(
        "/auth/token", json={"doctor_id": "dr-A", "password": TEST_DOCTOR_PASSWORD}
    )
    token = login.json()["access_token"]
    response = client.get("/patient/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
