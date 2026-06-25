"""Patient portal — the patient's own self-service surface (loop-closing UI).

The web app's other routers are the *doctor's* console. This one is the
**patient's**: they sign in with their patient id + PIN (the same caller-ID/PIN
identity the voice line uses), then see their approved care plan, ask the agent a
follow-up, and read the doctor's replies to anything that was escalated.

Every route is scoped by the authenticated :class:`PatientPrincipal`, so a patient
can only ever reach *their own* record under *their own* doctor — the same
one-patient isolation the rest of the system guarantees, enforced here by the JWT
subject rather than a request-body id.

Owner: Ruthwik (integration) — closes the escalation loop on the patient side.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from careline.adapters.auth.principals import PatientPrincipal
from careline.api.deps import get_current_patient
from careline.api.dto.patients import FactOut
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision
from careline.services.patient_lookup_service import hash_pin

router = APIRouter(prefix="/patient", tags=["patient-portal"])


# --- wire shapes -------------------------------------------------------------


class PatientLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: str
    pin: str


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
    model_config = ConfigDict(extra="forbid")

    verdict: str
    answer_text: str | None = None
    escalation_reason: str | None = None
    citations: list[str] = Field(default_factory=list)


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


# --- routes ------------------------------------------------------------------


@router.post("/login", response_model=PatientLoginOut)
async def patient_login(body: PatientLoginIn, request: Request) -> PatientLoginOut:
    """Authenticate a patient by patient id + PIN and issue a patient session token.

    Unknown patient and wrong PIN are the same 401 — no oracle that distinguishes
    "no such patient" from "wrong PIN".
    """
    settings = request.app.state.settings
    identity = await request.app.state.patient_repo.find_by_patient_id(
        patient_id=body.patient_id
    )
    unauthorized = HTTPException(status_code=401, detail="invalid patient id or PIN")
    if identity is None:
        raise unauthorized
    provided = hash_pin(pin=body.pin, secret=settings.pin_hmac_secret)
    if not hmac.compare_digest(provided, identity.pin_hmac):
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
    session = CallSession(
        call_id=f"portal-{principal.patient_id}",
        patient_id=principal.patient_id,
        doctor_id=principal.doctor_id,
        max_clarify_turns=2,
    )
    decision: Decision = request.app.state.question_svc.run_question(
        question=body.question, patient=patient, session=session, now=now
    )
    return PatientAnswerOut(
        verdict=decision.verdict.value,
        answer_text=decision.answer_text,
        escalation_reason=decision.escalation_reason,
        citations=list(decision.citations),
    )


@router.get("/questions", response_model=list[PatientQuestionOut])
async def patient_questions(
    request: Request,
    principal: Annotated[PatientPrincipal, Depends(get_current_patient)],
) -> list[PatientQuestionOut]:
    """The patient's past questions, newest first, with any doctor reply attached."""
    audit = request.app.state.audit
    turns = sorted(
        audit.turns_for_patient(principal.patient_id),
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
            )
        )
    return out


__all__ = ["router"]
