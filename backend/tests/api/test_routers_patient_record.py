"""Patient Record endpoint tests — valid slice + superseded history (#4 backend).

Backs Naga's Patient Record UI: GET /patients/{id}/record returns the currently
valid facts plus the retired history, tenant-scoped and no-leak.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from careline.domain.model.fact import Medication
from careline.domain.model.temporal import Validity

_PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MID = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _seed(client: TestClient, *, doctor_id: str, patient_id: str, facts) -> None:
    asyncio.run(
        client.app.state.patient_repo.add_facts(
            doctor_id=doctor_id, patient_id=patient_id, facts=facts
        )
    )


def _current_med() -> Medication:
    return Medication(
        id="med-current",
        validity=Validity(effective_from=_MID),
        summary="Take 500mg paracetamol every 6 hours.",
        name="paracetamol",
        dose="500mg",
    ).approve("dr-X", _MID)


def _superseded_med() -> Medication:
    return Medication(
        id="med-old",
        validity=Validity(effective_from=_PAST, superseded_at=_MID),
        summary="Take 250mg paracetamol every 8 hours.",
        name="paracetamol",
        dose="250mg",
    ).approve("dr-X", _PAST)


def test_record_requires_auth(client: TestClient):
    assert client.get("/patients/patient-A/record").status_code == 401


def test_record_unknown_patient_is_404(client: TestClient, dr_x_headers: dict[str, str]):
    res = client.get("/patients/ghost/record", headers=dr_x_headers)
    assert res.status_code == 404


def test_record_wrong_tenant_is_404_not_403(
    client: TestClient, dr_x_headers: dict[str, str], other_doctor_headers: dict[str, str]
):
    _seed(client, doctor_id="dr-X", patient_id="patient-A", facts=(_current_med(),))
    # dr-B asks for dr-X's patient → not found, never a leak.
    res = client.get("/patients/patient-A/record", headers=other_doctor_headers)
    assert res.status_code == 404


def test_record_separates_current_from_superseded(
    client: TestClient, dr_x_headers: dict[str, str]
):
    _seed(
        client,
        doctor_id="dr-X",
        patient_id="patient-A",
        facts=(_current_med(), _superseded_med()),
    )
    res = client.get("/patients/patient-A/record", headers=dr_x_headers)
    assert res.status_code == 200
    body = res.json()

    assert body["patient_id"] == "patient-A"
    assert body["doctor_id"] == "dr-X"

    current_ids = [f["id"] for f in body["current"]]
    history_ids = [f["id"] for f in body["history"]]
    # The open, approved fact is current; the closed one is history — never both.
    assert current_ids == ["med-current"]
    assert history_ids == ["med-old"]

    current = body["current"][0]
    assert current["kind"] == "medication"
    assert current["current"] is True
    assert current["superseded_at"] is None
    assert "500mg paracetamol" in current["summary"]

    retired = body["history"][0]
    assert retired["current"] is False
    assert retired["superseded_at"] is not None
