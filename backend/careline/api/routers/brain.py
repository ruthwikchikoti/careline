"""Internal brain endpoint — telephony bridge (NR-6 phase 2).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from careline.adapters.auth.principals import InternalPrincipal
from careline.api.deps import get_internal_principal
from careline.api.dto.brain import AnswerOut, QuestionIn, TraceStepOut
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision

router = APIRouter(prefix="/internal", tags=["brain"])


def _answer_out(decision: Decision) -> AnswerOut:
    return AnswerOut(
        verdict=decision.verdict,
        answer_text=decision.answer_text,
        escalation_reason=decision.escalation_reason,
        confidence=decision.confidence,
        risk=decision.risk,
        citations=list(decision.citations),
        trace=[
            TraceStepOut(
                name=step.name,
                status=step.status.value,
                spec_section=step.spec_section,
                detail=step.detail,
            )
            for step in decision.trace.steps
        ],
    )


@router.post("/run-question", response_model=AnswerOut)
async def run_question(
    body: QuestionIn,
    request: Request,
    _principal: Annotated[InternalPrincipal, Depends(get_internal_principal)],
) -> AnswerOut:
    """Run one patient question through the safety spine."""
    patient = await request.app.state.patient_repo.get(
        doctor_id=body.doctor_id,
        patient_id=body.patient_id,
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="not found")

    settings = request.app.state.settings
    session = CallSession(
        call_id=body.call_id,
        patient_id=body.patient_id,
        doctor_id=body.doctor_id,
        max_clarify_turns=settings.max_clarify_turns,
    )
    decision = request.app.state.question_svc.run_question(
        question=body.question,
        patient=patient,
        session=session,
    )
    return _answer_out(decision)
