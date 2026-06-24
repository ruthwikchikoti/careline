"""Console → audit bridge: demo turns surface in the doctor's queues (VI-7).

A red-flag question asked through the auth-free Live Console (``/demo/ask``)
must be recorded in the shared audit sink and show up in the signed-in doctor's
``/escalations`` and ``/audit`` views — and only that doctor's.

Owner: Vinay (scope ``eval``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from careline.combined import app as build_combined_app


@pytest.fixture()
def combined_client(monkeypatch) -> TestClient:
    # Force a deterministic, offline/keyless demo regardless of any local .env:
    # heuristic reasoning twins + in-memory repos (no live LLM, no Mongo).
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CARELINE_MONGO_URI", "MONGO_URI"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CARELINE_LLM_BACKEND", "heuristic")

    import careline.demo_server as demo
    from careline.adapters.orchestration.graph import build_default_graph

    monkeypatch.setattr(demo, "_graph", build_default_graph())

    import careline.api.app as app_module
    from careline.config import Settings

    monkeypatch.setattr(app_module, "get_settings", lambda: Settings(_env_file=None))

    with TestClient(build_combined_app(), raise_server_exceptions=False) as client:
        yield client


def _token(client: TestClient, doctor_id: str) -> dict[str, str]:
    response = client.post("/auth/token", json={"doctor_id": doctor_id})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_console_escalation_appears_in_doctor_queue(combined_client: TestClient):
    headers = _token(combined_client, "dr-console")

    ask = combined_client.post(
        "/demo/ask", headers=headers, json={"question": "I have chest pain"}
    )
    assert ask.status_code == 200
    assert ask.json()["verdict"] == "escalate"

    escalations = combined_client.get("/escalations", headers=headers)
    assert escalations.status_code == 200
    body = escalations.json()
    assert body["waiting"] >= 1
    assert any(turn["patient_id"] == "demo-patient" for turn in body["escalations"])

    audit = combined_client.get("/audit", headers=headers)
    assert audit.status_code == 200
    assert len(audit.json()["turns"]) >= 1


def test_console_turn_is_tenant_scoped(combined_client: TestClient):
    asker = _token(combined_client, "dr-asker")
    other = _token(combined_client, "dr-other")

    combined_client.post("/demo/ask", headers=asker, json={"question": "I have chest pain"})

    # The doctor who asked sees the escalation; a different doctor never does.
    assert combined_client.get("/escalations", headers=asker).json()["waiting"] >= 1
    assert combined_client.get("/escalations", headers=other).json()["waiting"] == 0


def test_anonymous_console_turn_still_works(combined_client: TestClient):
    # No Authorization header → endpoint stays usable (logged under demo-doctor).
    ask = combined_client.post("/demo/ask", json={"question": "paracetamol dose?"})
    assert ask.status_code == 200
    assert "verdict" in ask.json()
