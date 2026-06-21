"""Consultation router tests (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_consultation_happy_path(
    client: TestClient,
    authed_headers: dict[str, str],
    seeded_consultation: str,
):
    extract = client.post(
        f"/consultations/{seeded_consultation}/extract",
        headers=authed_headers,
    )
    assert extract.status_code == 200
    assert extract.json()["fact_count"] > 0

    approve = client.post(
        f"/consultations/{seeded_consultation}/approve",
        headers=authed_headers,
    )
    assert approve.status_code == 200
    payload = approve.json()
    assert payload["status"] == "approved"
    assert payload["applied_facts"] > 0


def test_create_consultation_rejects_doctor_id_in_body(
    client: TestClient,
    authed_headers: dict[str, str],
):
    response = client.post(
        "/consultations",
        headers=authed_headers,
        json={
            "patient_id": "patient-A",
            "doctor_id": "dr-evil",
            "transcript": "hello",
        },
    )
    assert response.status_code == 422


def test_extract_without_consent_returns_422(client: TestClient, authed_headers: dict[str, str]):
    create = client.post(
        "/consultations",
        headers=authed_headers,
        json={"patient_id": "patient-A", "transcript": "Prescribed Paracetamol 500mg twice daily."},
    )
    consultation_id = create.json()["consultation_id"]
    response = client.post(
        f"/consultations/{consultation_id}/extract",
        headers=authed_headers,
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "consent required"}


def test_approve_without_facts_returns_422(
    client: TestClient,
    authed_headers: dict[str, str],
    seeded_consultation: str,
):
    response = client.post(
        f"/consultations/{seeded_consultation}/approve",
        headers=authed_headers,
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "no facts to approve"}


def test_cross_doctor_consultation_fetch_returns_404(
    client: TestClient,
    authed_headers: dict[str, str],
    other_doctor_headers: dict[str, str],
):
    create = client.post(
        "/consultations",
        headers=authed_headers,
        json={"patient_id": "patient-A", "transcript": "hello"},
    )
    consultation_id = create.json()["consultation_id"]
    response = client.get(
        f"/consultations/{consultation_id}",
        headers=other_doctor_headers,
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
