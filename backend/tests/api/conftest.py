"""Shared fixtures for API router tests (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from careline.api.app import create_app
from careline.config import Settings

_DR_A = "dr-A"
_DR_B = "dr-B"
_PATIENT = "patient-A"
_PURPOSE = "post-consultation follow-up answering"
_TRANSCRIPT = (
    "Prescribed Paracetamol 500mg twice daily. "
    "Patient should rest for one week post surgery."
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def client() -> TestClient:
    """Offline TestClient — in-memory lifespan, no server exceptions."""
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture()
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def dr_x_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/auth/token", json={"doctor_id": "dr-X"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def authed_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/auth/token", json={"doctor_id": _DR_A})
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def other_doctor_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/auth/token", json={"doctor_id": _DR_B})
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def internal_headers(settings: Settings) -> dict[str, str]:
    return {"X-Internal-Key": settings.internal_api_key}


@pytest.fixture()
def seeded_consultation(client: TestClient, authed_headers: dict[str, str]) -> str:
    create = client.post(
        "/consultations",
        headers=authed_headers,
        json={"patient_id": _PATIENT, "transcript": _TRANSCRIPT},
    )
    assert create.status_code == 201
    consultation_id = create.json()["consultation_id"]
    consent = client.post(
        f"/consultations/{consultation_id}/consent",
        headers=authed_headers,
        json={"purpose": _PURPOSE},
    )
    assert consent.status_code == 200
    return consultation_id


async def _seed_patient(client: TestClient, *, doctor_id: str, patient_id: str, facts):
    await client.app.state.patient_repo.add_facts(
        doctor_id=doctor_id,
        patient_id=patient_id,
        facts=facts,
    )


@pytest.fixture()
def seeded_patient(client: TestClient):
    from careline.adapters.memory.seed import seed_patient

    patient = seed_patient()
    _run(_seed_patient(client, doctor_id=patient.doctor_id, patient_id=patient.patient_id, facts=patient.facts))
    return patient
