"""Patient portal — the patient's own self-service surface (loop-closing UI).

The web app's other routers are the *doctor's* console. This one is the
**patient's**: they sign in with their clinic/doctor id + patient id + PIN (the
same caller-ID/PIN identity the voice line uses), then see their approved care
plan, ask the agent a follow-up, and read the doctor's replies to anything that
was escalated.

Every route is scoped by the authenticated :class:`PatientPrincipal`, so a patient
can only ever reach *their own* record under *their own* doctor — the same
one-patient isolation the rest of the system guarantees, enforced here by the JWT
subject rather than a request-body id. ``patient_id`` is only unique *within* a
doctor, so login and every audit read are keyed by ``(doctor_id, patient_id)``
(REVIEW-1): two doctors may both have a "p2" and neither can reach the other's.

Owner: Ruthwik (integration) — closes the escalation loop on the patient side.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from careline.adapters.auth.principals import PatientPrincipal
from careline.api.deps import get_current_patient
from careline.api.dto.patients import FactOut
from careline.api.login_throttle import guard_login, record_login
from careline.domain.enums import ScopeCategory
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision
from careline.services.auth_service import is_reserved_doctor_id, is_reserved_patient_id
from careline.services.patient_lookup_service import hash_pin

router = APIRouter(prefix="/patient", tags=["patient-portal"])


# --- wire shapes -------------------------------------------------------------


class PatientLoginIn(BaseModel):
    """Tenant-scoped portal login: clinic/doctor id + patient id + PIN."""

    model_config = ConfigDict(extra="forbid")

    doctor_id: str = Field(min_length=1, max_length=128)
    patient_id: str = Field(min_length=1, max_length=128)
    pin: str = Field(min_length=1, max_length=32)


class PatientLoginOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
    patient_id: str
    doctor_id: str


class CarePlanOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: str
    as_of: datetime
    facts: list[FactOut] = Field(default_factory=list)


class PatientAskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)


class PatientAnswerOut(BaseModel):
    """The agent's reply to one portal question.

    ``patient_message`` is the decision's patient-facing text (the answer, the
    clarifying redirect, or the escalation notice) so the UI never has to guess
    what to show; ``emergency`` is true for a RED_FLAG turn, which the portal
    renders with a prominent call-112 banner.
    """

    model_config = ConfigDict(extra="forbid")

    verdict: str
    answer_text: str | None = None
    escalation_reason: str | None = None
    citations: list[str] = Field(default_factory=list)
    patient_message: str | None = None
    emergency: bool = False


class PatientQuestionOut(BaseModel):
    """One question the patient asked + how it resolved (incl. a doctor reply)."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    asked_at: datetime
    question: str | None = None
    verdict: str
    answer_text: str | None = None
    escalated: bool = False
    doctor_reply: str | None = None
    replied_at: datetime | None = None
    patient_message: str | None = None
    emergency: bool = False


# --- routes ------------------------------------------------------------------


@router.post("/login", response_model=PatientLoginOut)
async def patient_login(body: PatientLoginIn, request: Request) -> PatientLoginOut:
    """Authenticate a patient by doctor id + patient id + PIN; issue a session token.

    Unknown doctor, unknown patient, reserved demo id and wrong PIN are the same
    401 — no oracle that distinguishes them. The identity lookup is tenant-scoped,
    so a PIN only ever opens the patient registered under the named doctor.
    Failed attempts are counted per (doctor, patient) and per client IP; past the
    threshold every attempt is a 429 for the lockout window (REVIEW-4).
    """
    settings = request.app.state.settings
    unauthorized = HTTPException(status_code=401, detail="invalid patient id or PIN")
    account = f"patient:{body.doctor_id}:{body.patient_id}"
    ip = guard_login(request, account=account)

    identity = None
    if not (is_reserved_doctor_id(body.doctor_id) or is_reserved_patient_id(body.patient_id)):
        identity = await request.app.state.patient_repo.find_identity(
            doctor_id=body.doctor_id, patient_id=body.patient_id
        )
    provided = hash_pin(pin=body.pin, secret=settings.pin_hmac_secret)
    ok = (
        identity is not None
        and identity.doctor_id == body.doctor_id
        and hmac.compare_digest(provided, identity.pin_hmac)
    )
    record_login(request, account=account, ip=ip, ok=ok)
    if not ok or identity is None:
        raise unauthorized
    token = request.app.state.auth_svc.issue_patient_token(
        patient_id=identity.patient_id, doctor_id=identity.doctor_id
    )
    return PatientLoginOut(
        access_token=token, patient_id=identity.patient_id, doctor_id=identity.doctor_id
    )


@router.get("/me", response_model=CarePlanOut)
async def patient_care_plan(
    request: Request,
    principal: Annotated[PatientPrincipal, Depends(get_current_patient)],
) -> CarePlanOut:
    """The patient's approved, currently-valid facts — their care plan."""
    now = datetime.now(timezone.utc)
    valid = await request.app.state.patient_repo.valid_slice(
        doctor_id=principal.doctor_id, patient_id=principal.patient_id, now=now
    )
    return CarePlanOut(
        patient_id=principal.patient_id,
        as_of=now,
        facts=[FactOut.from_fact(f, current=True) for f in valid.facts],
    )


@router.post("/ask", response_model=PatientAnswerOut)
async def patient_ask(
    body: PatientAskIn,
    request: Request,
    principal: Annotated[PatientPrincipal, Depends(get_current_patient)],
) -> PatientAnswerOut:
    """Run the patient's follow-up through the safety spine, scoped to themselves."""
    now = datetime.now(timezone.utc)
    patient = await request.app.state.patient_repo.get(
        doctor_id=principal.doctor_id, patient_id=principal.patient_id
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="patient record not found")
    # One-shot web turn: no clarify budget, so a clinical question we can't ground
    # goes straight to the doctor (who replies via the resolution loop) rather than
    # looping on "could you rephrase?". Non-clinical input is still redirected.
    session = CallSession(
        call_id=f"portal-{principal.patient_id}",
        patient_id=principal.patient_id,
        doctor_id=principal.doctor_id,
        max_clarify_turns=0,
    )
    # The pipeline is synchronous (graph + possible blocking LLM call + audit
    # write): run it on the threadpool so it never blocks the event loop.
    decision: Decision = await run_in_threadpool(
        request.app.state.question_svc.run_question,
        question=body.question,
        patient=patient,
        session=session,
        now=now,
    )
    return PatientAnswerOut(
        verdict=decision.verdict.value,
        answer_text=decision.answer_text,
        escalation_reason=decision.escalation_reason,
        citations=list(decision.citations),
        patient_message=decision.answer_text or decision.escalation_reason,
        emergency=decision.scope is ScopeCategory.RED_FLAG,
    )


@router.delete("/history", status_code=204)
async def clear_patient_history(
    request: Request,
    principal: Annotated[PatientPrincipal, Depends(get_current_patient)],
) -> None:
    """Clear the signed-in patient's own question history (after a practice run).

    Scoped to the JWT subject *and* its doctor, so a patient can only ever clear
    *their own* thread — never a same-named patient under another doctor. The care
    plan and the patient's record are untouched; only the Q&A thread is removed.
    """
    request.app.state.audit.clear_patient(
        doctor_id=principal.doctor_id, patient_id=principal.patient_id
    )


@router.get("/questions", response_model=list[PatientQuestionOut])
async def patient_questions(
    request: Request,
    principal: Annotated[PatientPrincipal, Depends(get_current_patient)],
) -> list[PatientQuestionOut]:
    """The patient's past questions, newest first, with any doctor reply attached."""
    audit = request.app.state.audit
    turns = sorted(
        audit.turns_for_patient(
            doctor_id=principal.doctor_id, patient_id=principal.patient_id
        ),
        key=lambda t: t.logged_at,
        reverse=True,
    )
    out: list[PatientQuestionOut] = []
    for t in turns:
        resolution = audit.resolution_for(t.turn_id)
        out.append(
            PatientQuestionOut(
                turn_id=t.turn_id,
                asked_at=t.logged_at,
                question=t.question,
                verdict=t.verdict.value,
                answer_text=t.answer_text,
                escalated=t.verdict.value == "escalate",
                doctor_reply=resolution.reply_text if resolution else None,
                replied_at=resolution.resolved_at if resolution else None,
                patient_message=t.answer_text or t.escalation_reason,
                emergency=t.scope == ScopeCategory.RED_FLAG.value,
            )
        )
    return out


__all__ = ["router"]
