"""Keyless dose change, end to end through the API (final evaluator finding
``redteam-keyless-supersede-not-recordable``): consultation 1 prescribes
Metformin 1000mg; consultation 2 "Reduce Metformin to 500mg ..." now extracts
one fact keyless, approval supersedes the old dose, and the record shows only
500mg as current with 1000mg in history. Unit cases:
``tests/brain/test_heuristic_dose_change.py``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.conftest import _PATIENT, _PURPOSE


def _consult(client, headers, transcript):
    cid = client.post("/consultations", headers=headers,
                      json={"patient_id": _PATIENT, "transcript": transcript}).json()[
        "consultation_id"]
    assert client.post(f"/consultations/{cid}/consent", headers=headers,
                       json={"purpose": _PURPOSE}).status_code == 200
    ext = client.post(f"/consultations/{cid}/extract", headers=headers)
    assert ext.status_code == 200, ext.text
    assert ext.json()["fact_count"] == 1
    app = client.post(f"/consultations/{cid}/approve", headers=headers)
    assert app.status_code == 200, app.text


def test_keyless_dose_reduction_supersedes_the_old_dose_end_to_end(
    client: TestClient, authed_headers: dict[str, str]
):
    _consult(client, authed_headers, "Prescribed Metformin 1000mg twice daily.")
    _consult(client, authed_headers,
             "Reduce Metformin to 500mg twice daily. Stop the 1000mg dose.")
    rec = client.get(f"/patients/{_PATIENT}/record", headers=authed_headers)
    assert rec.status_code == 200, rec.text
    body = rec.json()
    current = [f["summary"] for f in body["current"] if f["kind"] == "medication"]
    history = [f["summary"] for f in body["history"] if f["kind"] == "medication"]
    assert current == ["Metformin 500mg twice daily"]
    assert any("1000mg" in s for s in history)
