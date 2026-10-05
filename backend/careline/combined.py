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

# Load .env BEFORE importing the app, so the observability adapter (which reads
# LANGSMITH_API_KEY at import time) sees it on the standard
# ``uvicorn careline.combined:app --factory`` launch — without this, tracing is
# imported before the key is in the environment and silently stays disabled. The
# offline test suite never imports this module, so it remains keyless/trace-free.
# Optional dependency — never fatal.
try:  # pragma: no cover - trivial optional import wiring
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv()
except Exception:
    pass

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

    # Use a real registered patient (Mongo-persisted facts) when one is named and
    # we have a signed-in doctor; otherwise fall back to the bundled demo patient.
    from datetime import datetime, timezone

    patient = None
    patient_id = "demo-patient"
    now = _NOW
    if body.patient_id and doctor_id != "demo-doctor":
        try:
            patient = await request.app.state.patient_repo.get(
                doctor_id=doctor_id, patient_id=body.patient_id
            )
        except Exception:
            patient = None
        if patient is not None:
            patient_id = body.patient_id
            now = datetime.now(timezone.utc)
    if patient is None:
        patient = _demo_patient()

    # One-shot web turn (no multi-turn voice loop): with no clarify budget, a
    # clinical question we can't ground goes straight to the doctor rather than
    # looping on "could you rephrase?". Non-clinical input is still redirected by
    # the scope gate, and greetings by the conversational rail.
    session = CallSession(
        call_id="web-demo",
        patient_id=patient_id,
        doctor_id=doctor_id,
        max_clarify_turns=0,
    )
    decision = request.app.state.question_svc.run_question(
        question=body.question,
        patient=patient,
        session=session,
        now=now,
    )
    return _decision_payload(decision)


def app():
    """Factory: the real app plus the demo console endpoints.

    The demo routes now live inside ``create_app`` itself (mounted for any non-prod
    entrypoint), so this factory is just an explicit alias kept for back-compat —
    ``uvicorn careline.combined:app --factory`` and ``careline.api.app:create_app``
    are now equivalent.
    """
    return create_app()


__all__ = ["app"]
