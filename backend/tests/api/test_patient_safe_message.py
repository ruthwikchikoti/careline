"""Patient-facing text never shows internal gate reasons (final evaluator
finding ``redteam-internal-reason-shown-to-patient``).

At v7 POST /patient/ask "how do I take metformin?" returned patient_message
"Risk too high (0.78) — escalating to doctor." — internal gate wording and a
numeric score, rendered verbatim by the portal. Now every patient-facing
message for an ESCALATE is a fixed plain text (with the 112 line; an
emergency-first variant for a RED_FLAG turn); the internal reason stays in
the audit / doctor view only and is not sent to the patient.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from careline.api.patient_text import (
    PATIENT_EMERGENCY_MESSAGE,
    PATIENT_ESCALATION_MESSAGE,
    patient_message,
)
from careline.domain.enums import ScopeCategory, Verdict
from careline.domain.gates.chain import EMERGENCY_LINE
from tests.api.conftest import doctor_headers

_INTERNAL = re.compile(r"\d\.\d|\brisk\b|\bconfidence\b|\bceiling\b|\bthreshold\b|escalating",
                       re.IGNORECASE)


@pytest.mark.parametrize(
    "reason",
    [
        "Risk too high (0.78) — escalating to doctor.",
        "risk 0.78 > ceiling 0.75",
        "Confidence too low to answer safely — transferring to your doctor.",
        "Answer not fully supported by patient record: ['1000mg']",
        "Independent verification unavailable — escalating for safety.",
    ],
)
def test_non_emergency_escalation_reason_is_replaced(reason):
    msg = patient_message(Verdict.ESCALATE, None, reason, ScopeCategory.IN_SCOPE)
    assert msg == PATIENT_ESCALATION_MESSAGE
    assert not _INTERNAL.search(msg)
    assert EMERGENCY_LINE in msg


def test_emergency_escalation_gets_the_emergency_first_message():
    msg = patient_message(Verdict.ESCALATE, None, "Red-flag detected: chest pain. x",
                          "red_flag")
    assert msg == PATIENT_EMERGENCY_MESSAGE
    assert "112" in msg and not _INTERNAL.search(msg)


def test_answer_and_clarify_text_pass_through_unless_internal():
    assert patient_message(Verdict.ANSWER, "Take paracetamol 500mg twice daily.", None,
                           ScopeCategory.IN_SCOPE) == "Take paracetamol 500mg twice daily."
    clarify = f"Could you rephrase? {EMERGENCY_LINE}"
    assert patient_message(Verdict.CLARIFY, clarify, None, None) == clarify
    # A text carrying an internal score never reaches the patient.
    assert patient_message(Verdict.CLARIFY, "confidence 0.42 below threshold", None,
                           None) == PATIENT_ESCALATION_MESSAGE


_DOC, _PID, _PIN = "dr-safe", "p-safe", "582913"


def _portal(client: TestClient) -> dict[str, str]:
    doctor = doctor_headers(client, _DOC)
    reg = client.post("/patients", headers=doctor,
                      json={"patient_id": _PID, "caller_id": "+910000000078", "pin": _PIN})
    assert reg.status_code == 201, reg.text
    login = client.post("/patient/login",
                        json={"doctor_id": _DOC, "patient_id": _PID, "pin": _PIN})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_portal_never_sends_internal_reasons_to_the_patient(client: TestClient):
    portal = _portal(client)
    body = client.post("/patient/ask", headers=portal,
                       json={"question": "how do I take metformin?"}).json()
    assert body["verdict"] == "escalate"
    assert body["patient_message"] == PATIENT_ESCALATION_MESSAGE
    assert body["escalation_reason"] is None
    (turn,) = client.get("/patient/questions", headers=portal).json()
    assert turn["patient_message"] == PATIENT_ESCALATION_MESSAGE
    # The internal reason is still recorded for the doctor / audit.
    esc = client.get("/escalations", headers=doctor_headers(client, _DOC))
    assert esc.status_code == 200
    assert any(e.get("escalation_reason") for e in esc.json()["escalations"])


def test_portal_emergency_turn_shows_the_112_message(client: TestClient):
    portal = _portal(client)
    body = client.post("/patient/ask", headers=portal,
                       json={"question": "I have crushing chest pain right now"}).json()
    assert body["emergency"] is True
    assert body["patient_message"] == PATIENT_EMERGENCY_MESSAGE
    assert body["escalation_reason"] is None


def test_demo_ask_carries_a_plain_patient_message(client: TestClient):
    body = client.post("/demo/ask", json={"question": "how do I take metformin?"}).json()
    assert "patient_message" in body
    if body["verdict"] == "escalate":
        assert not _INTERNAL.search(body["patient_message"])
