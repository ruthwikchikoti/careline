"""DPDP erasure router tests (NR-7).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_erase_patient_happy_path(
    client: TestClient,
    dr_x_headers: dict[str, str],
    seeded_patient,
):
    response = client.delete("/patients/patient-A/data", headers=dr_x_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["patient_id"] == "patient-A"
    assert payload["layer1_nulled"] >= 0
    assert payload["layer2_dropped"] is True
    assert "pin_hmac" not in payload


def test_erase_patient_not_findable_after_erasure(
    client: TestClient,
    dr_x_headers: dict[str, str],
    seeded_patient,
):
    erase = client.delete("/patients/patient-A/data", headers=dr_x_headers)
    assert erase.status_code == 200
    get = client.get("/patients/patient-A", headers=dr_x_headers)
    assert get.status_code == 404
    assert get.json() == {"detail": "not found"}


def test_erase_missing_patient_returns_404(
    client: TestClient,
    authed_headers: dict[str, str],
):
    response = client.delete("/patients/patient-A/data", headers=authed_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}


def test_erase_wrong_tenant_returns_404_not_403(
    client: TestClient,
    other_doctor_headers: dict[str, str],
    seeded_patient,
):
    response = client.delete("/patients/patient-A/data", headers=other_doctor_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}


def test_erase_without_authorization_returns_401(client: TestClient, seeded_patient):
    response = client.delete("/patients/patient-A/data")
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}
