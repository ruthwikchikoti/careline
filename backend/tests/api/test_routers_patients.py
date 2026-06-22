"""Patient router tests (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_patient_without_data_returns_404(client: TestClient, authed_headers: dict[str, str]):
    response = client.get("/patients/patient-A", headers=authed_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}


def test_get_patient_wrong_tenant_returns_404_not_403(
    client: TestClient,
    authed_headers: dict[str, str],
    other_doctor_headers: dict[str, str],
    seeded_patient,
):
    response = client.get("/patients/patient-A", headers=other_doctor_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}


def test_get_patient_missing_authorization_returns_401(client: TestClient):
    response = client.get("/patients/patient-A")
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_get_patient_happy_path(client: TestClient, dr_x_headers: dict[str, str], seeded_patient):
    response = client.get("/patients/patient-A", headers=dr_x_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["patient_id"] == "patient-A"
    assert payload["doctor_id"] == "dr-X"
    assert payload["fact_count"] > 0
    assert "pin_hmac" not in payload


def test_register_patient_happy_path(client: TestClient, authed_headers: dict[str, str]):
    response = client.post(
        "/patients",
        headers=authed_headers,
        json={
            "patient_id": "patient-new",
            "caller_id": "+919876543210",
            "pin": "1234",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["patient_id"] == "patient-new"
    assert payload["doctor_id"] == "dr-A"
    assert payload["fact_count"] == 0
    assert "pin_hmac" not in payload


def test_register_patient_rejects_doctor_id_in_body(
    client: TestClient,
    authed_headers: dict[str, str],
):
    response = client.post(
        "/patients",
        headers=authed_headers,
        json={
            "patient_id": "patient-new",
            "caller_id": "+919876543210",
            "pin": "1234",
            "doctor_id": "dr-evil",
        },
    )
    assert response.status_code == 422


def test_register_patient_without_auth_returns_401(client: TestClient):
    response = client.post(
        "/patients",
        json={
            "patient_id": "patient-new",
            "caller_id": "+919876543210",
            "pin": "1234",
        },
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}
