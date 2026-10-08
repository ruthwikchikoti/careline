"""GET /monitoring states its scope honestly (redteam-monitoring-cross-tenant).

The snapshot is process-wide: every authenticated doctor sees the deployment's
aggregate volume / verdict mix, not a per-tenant slice. Rather than imply
otherwise, the response says so in a top-level ``scope`` field, and it must
never carry question text or patient identifiers.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_monitoring_declares_process_wide_aggregate_scope(
    client: TestClient, authed_headers: dict[str, str]
):
    body = client.get("/monitoring", headers=authed_headers).json()
    assert body["scope"] == "process-wide, aggregate, no PHI"


def test_monitoring_never_carries_question_text_or_patient_ids(
    client: TestClient, authed_headers: dict[str, str], other_doctor_headers: dict[str, str]
):
    ask = client.post("/demo/ask", json={"question": "zebra-marker-question about my diet"})
    assert ask.status_code == 200
    text = client.get("/monitoring", headers=other_doctor_headers).text
    assert "zebra-marker-question" not in text
    assert "demo-patient" not in text and "dr-A" not in text


def test_monitoring_requires_a_doctor_session(client: TestClient):
    assert client.get("/monitoring").status_code == 401
