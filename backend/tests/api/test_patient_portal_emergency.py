"""Patient portal shows the agent's own message and flags emergencies (redteam).

The portal used to discard the decision text and render every escalated turn as
"Sent to your doctor", so a patient reporting crushing chest pain was never shown
the emergency guidance. ``PatientAnswerOut`` / ``PatientQuestionOut`` now carry
``patient_message`` (the decision's patient-facing text) and ``emergency``
(scope RED_FLAG) so the web page can show a prominent call-112 banner. Since
the final evaluator pass the message for an ESCALATE is a fixed plain text
(``careline.api.patient_text``), never the internal gate reason.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from careline.api.patient_text import PATIENT_EMERGENCY_MESSAGE, PATIENT_ESCALATION_MESSAGE
from tests.api.conftest import doctor_headers

_DOC = "dr-portal"
_PID = "p-portal"
_PIN = "482913"


def _portal(client: TestClient) -> dict[str, str]:
    doctor = doctor_headers(client, _DOC)
    reg = client.post(
        "/patients",
        headers=doctor,
        json={"patient_id": _PID, "caller_id": "+910000000077", "pin": _PIN},
    )
    assert reg.status_code == 201, reg.text
    login = client.post(
        "/patient/login", json={"doctor_id": _DOC, "patient_id": _PID, "pin": _PIN}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_red_flag_turn_is_marked_emergency_with_its_message(client: TestClient):
    portal = _portal(client)
    ask = client.post(
        "/patient/ask", headers=portal, json={"question": "I have crushing chest pain right now"}
    )
    assert ask.status_code == 200, ask.text
    body = ask.json()
    assert body["verdict"] == "escalate"
    assert body["emergency"] is True
    # A fixed plain emergency message (112 first) — never the internal reason.
    assert body["patient_message"] == PATIENT_EMERGENCY_MESSAGE
    assert body["escalation_reason"] is None

    (turn,) = client.get("/patient/questions", headers=portal).json()
    assert turn["emergency"] is True
    assert turn["patient_message"] == body["patient_message"]


def test_non_emergency_turn_is_not_flagged_and_carries_its_text(client: TestClient):
    portal = _portal(client)
    ask = client.post(
        "/patient/ask", headers=portal, json={"question": "When is my next appointment?"}
    )
    assert ask.status_code == 200, ask.text
    body = ask.json()
    assert body["emergency"] is False
    expected = body["answer_text"] if body["verdict"] != "escalate" else PATIENT_ESCALATION_MESSAGE
    assert body["patient_message"] == expected

    (turn,) = client.get("/patient/questions", headers=portal).json()
    assert turn["emergency"] is False
    assert turn["patient_message"] == expected
