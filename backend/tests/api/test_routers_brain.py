"""Brain / run-question router tests (NR-6 phase 2).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _question_payload(*, doctor_id: str = "dr-X", patient_id: str = "patient-A") -> dict[str, str]:
    return {
        "doctor_id": doctor_id,
        "patient_id": patient_id,
        "call_id": "call-001",
        "question": "What diet should I follow?",
    }


def test_run_question_happy_path(
    client: TestClient,
    internal_headers: dict[str, str],
    seeded_patient,
):
    response = client.post(
        "/internal/run-question",
        headers=internal_headers,
        json=_question_payload(),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["verdict"] in {"answer", "clarify", "escalate"}
    assert isinstance(payload["trace"], list)


def test_run_question_missing_internal_key_returns_401(client: TestClient, seeded_patient):
    response = client.post(
        "/internal/run-question",
        json=_question_payload(),
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_run_question_wrong_internal_key_returns_401(client: TestClient, seeded_patient):
    response = client.post(
        "/internal/run-question",
        headers={"X-Internal-Key": "wrong-key"},
        json=_question_payload(),
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_run_question_unknown_patient_returns_404(
    client: TestClient,
    internal_headers: dict[str, str],
):
    response = client.post(
        "/internal/run-question",
        headers=internal_headers,
        json=_question_payload(patient_id="missing-patient"),
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}


def test_run_question_red_flag_escalates(
    client: TestClient,
    internal_headers: dict[str, str],
    seeded_patient,
):
    response = client.post(
        "/internal/run-question",
        headers=internal_headers,
        json={
            **_question_payload(),
            "question": "I have severe chest pain",
        },
    )
    assert response.status_code == 200
    assert response.json()["verdict"] == "escalate"


def test_run_question_wrong_tenant_doctor_id_returns_404(
    client: TestClient,
    internal_headers: dict[str, str],
    seeded_patient,
):
    response = client.post(
        "/internal/run-question",
        headers=internal_headers,
        json=_question_payload(doctor_id="dr-B"),
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
