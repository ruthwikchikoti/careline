"""Audit / escalations / eval read-endpoint tests (VI-7 · task #5).

Owner: Vinay (scope ``eval``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _log_turn(client: TestClient, *, doctor_id: str, verdict_escalate: bool) -> None:
    """Seed one audit turn directly via the shared in-memory AuditService."""
    from careline.domain.enums import Verdict
    from careline.domain.model.decision import Decision, ReasoningTrace

    decision = Decision(
        verdict=Verdict.ESCALATE if verdict_escalate else Verdict.ANSWER,
        answer_text=None if verdict_escalate else "Take Paracetamol 500mg twice daily.",
        escalation_reason="red flag" if verdict_escalate else None,
        confidence=0.2 if verdict_escalate else 0.91,
        risk=0.9 if verdict_escalate else 0.1,
        trace=ReasoningTrace(steps=()),
        citations=(),
    )
    client.app.state.audit.log_turn(
        call_id=f"call-{doctor_id}",
        patient_id="patient-A",
        doctor_id=doctor_id,
        question="what should I take?",
        decision=decision,
    )


def test_audit_requires_auth(client: TestClient):
    assert client.get("/audit").status_code == 401


def test_audit_returns_only_callers_turns(
    client: TestClient,
    authed_headers: dict[str, str],
    other_doctor_headers: dict[str, str],
):
    _log_turn(client, doctor_id="dr-A", verdict_escalate=False)
    _log_turn(client, doctor_id="dr-B", verdict_escalate=True)

    response = client.get("/audit", headers=authed_headers)
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["turns"]) == 1
    assert payload["turns"][0]["verdict"] == "answer"
    # dr-A must never see dr-B's trail (cross-tenant isolation).
    assert all(turn["patient_id"] == "patient-A" for turn in payload["turns"])


def test_escalations_filters_to_escalate_verdict(
    client: TestClient,
    authed_headers: dict[str, str],
):
    _log_turn(client, doctor_id="dr-A", verdict_escalate=False)
    _log_turn(client, doctor_id="dr-A", verdict_escalate=True)

    response = client.get("/escalations", headers=authed_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["waiting"] == 1
    assert len(payload["escalations"]) == 1
    assert payload["escalations"][0]["verdict"] == "escalate"


def test_audit_events_requires_auth(client: TestClient):
    assert client.get("/audit/events").status_code == 401


def test_audit_events_are_doctor_scoped(
    client: TestClient,
    authed_headers: dict[str, str],
):
    from careline.services.audit_service import AuditEventKind

    audit = client.app.state.audit
    audit.log_event(AuditEventKind.CONSENT, doctor_id="dr-A", detail="dr-A consent")
    audit.log_event(AuditEventKind.CONSENT, doctor_id="dr-B", detail="dr-B consent")
    audit.log_event(AuditEventKind.SYSTEM, detail="global system event")

    response = client.get("/audit/events", headers=authed_headers)
    assert response.status_code == 200
    details = {event["detail"] for event in response.json()}
    # dr-A sees its own event + the unattributed system event, never dr-B's.
    assert "dr-A consent" in details
    assert "global system event" in details
    assert "dr-B consent" not in details


def test_eval_runs_live_and_reports_results(
    client: TestClient,
    authed_headers: dict[str, str],
):
    response = client.get("/eval", headers=authed_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == len(payload["scenarios"]) > 0
    assert 0 <= payload["passed"] <= payload["total"]
    assert all("verdict" in s for s in payload["scenarios"])
