"""Combined dev entrypoint — real product API + demo console on one port (RU-6).

The web UI uses a single base URL (``NEXT_PUBLIC_API_BASE``). In production the
Live Console would call the authenticated ``/internal/run-question`` against a
registered patient; for a zero-setup demo it calls the keyless ``/demo/*``
endpoints instead. This module serves **both** from one FastAPI app so the entire
UI — login, patients, consultations, audit, *and* the Live Console — works
against ``localhost:8000`` without juggling two servers.

    cd backend && uvicorn careline.combined:app --factory --reload

Owner: Ruthwik (scope ``graph``/integration). Wraps Naresh's ``create_app`` —
does not modify it — and mounts the demo router from ``demo_server``.
"""

from __future__ import annotations

from fastapi import Request

from careline.api.app import create_app
from careline.demo_server import AskIn, _NOW, _demo_patient, demo_patient
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision


def _decision_payload(decision: Decision) -> dict:
    return {
        "verdict": decision.verdict.value,
        "answer_text": decision.answer_text,
        "escalation_reason": decision.escalation_reason,
        "confidence": decision.confidence,
        "risk": decision.risk,
        "citations": list(decision.citations),
        "trace": [
            {
                "name": s.name,
                "status": s.status.value,
                "spec_section": s.spec_section,
                "detail": s.detail,
            }
            for s in decision.trace.steps
        ],
    }


async def demo_ask(request: Request, body: AskIn) -> dict:
    """Run the demo question through QuestionService so audit/escalations populate.

    When the browser sends a doctor JWT (logged-in Live Console), turns are
    recorded under that doctor_id so ``GET /audit`` and ``GET /escalations``
    show them. Without a token, falls back to ``demo-doctor``.
    """
    doctor_id = "demo-doctor"
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        try:
            doctor_id = request.app.state.auth_svc.authenticate_doctor(token).doctor_id
        except Exception:
            pass

    session = CallSession(
        call_id="web-demo",
        patient_id="demo-patient",
        doctor_id=doctor_id,
        max_clarify_turns=2,
    )
    decision = request.app.state.question_svc.run_question(
        question=body.question,
        patient=_demo_patient(),
        session=session,
        now=_NOW,
    )
    return _decision_payload(decision)


def app():
    """Factory: the real app plus the demo console endpoints."""
    application = create_app()
    application.add_api_route("/demo/patient", demo_patient, methods=["GET"], tags=["demo"])
    application.add_api_route("/demo/ask", demo_ask, methods=["POST"], tags=["demo"])
    return application


__all__ = ["app"]
