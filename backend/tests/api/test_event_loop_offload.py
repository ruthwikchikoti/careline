"""The synchronous safety pipeline never runs on the event loop (REVIEW-8).

``QuestionService.run_question`` is synchronous (graph + possibly a blocking LLM
HTTP call + a sync audit write). Called directly inside an ``async def`` route it
blocks the whole event loop — every other request stalls behind one question.
Each question route must hand it to the threadpool; behaviour (the verdict and
payload) is unchanged.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import _run, _seed_patient, doctor_headers


@pytest.fixture()
def loop_probe(client: TestClient):
    """Wrap run_question to record whether it ran on an event-loop thread."""
    svc = client.app.state.question_svc
    original = svc.run_question
    calls: list[bool] = []

    def probe(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            calls.append(True)  # on the loop — blocking it
        except RuntimeError:
            calls.append(False)  # worker thread — loop stays free
        return original(*args, **kwargs)

    svc.run_question = probe  # type: ignore[method-assign]
    yield calls
    svc.run_question = original  # type: ignore[method-assign]


def test_demo_ask_runs_the_pipeline_off_the_event_loop(client, loop_probe):
    response = client.post("/demo/ask", json={"question": "Can I take paracetamol?"})
    assert response.status_code == 200
    assert response.json()["verdict"] in {"answer", "clarify", "escalate"}
    assert loop_probe == [False]


def test_internal_run_question_runs_off_the_event_loop(
    client, loop_probe, seeded_patient, internal_headers
):
    response = client.post(
        "/internal/run-question",
        headers=internal_headers,
        json={
            "call_id": "call-1",
            "doctor_id": seeded_patient.doctor_id,
            "patient_id": seeded_patient.patient_id,
            "question": "What is my paracetamol dose?",
        },
    )
    assert response.status_code == 200, response.text
    assert loop_probe == [False]


def test_patient_ask_runs_off_the_event_loop(client, loop_probe):
    headers = doctor_headers(client, "dr-asha")
    reg = client.post(
        "/patients",
        headers=headers,
        json={"patient_id": "p-loop", "caller_id": "+91555", "pin": "135790"},
    )
    assert reg.status_code == 201
    _run(_seed_patient(client, doctor_id="dr-asha", patient_id="p-loop", facts=()))
    login = client.post(
        "/patient/login",
        json={"doctor_id": "dr-asha", "patient_id": "p-loop", "pin": "135790"},
    )
    token = login.json()["access_token"]
    response = client.post(
        "/patient/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "When should I take my tablets?"},
    )
    assert response.status_code == 200
    assert loop_probe == [False]
