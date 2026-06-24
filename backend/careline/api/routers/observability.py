"""Audit / escalations / eval read routes (VI-7 · task #5).

Authenticated, doctor-scoped GET endpoints over the existing
:class:`~careline.services.audit_service.AuditService` and the offline eval
re-run.  These add no safety logic — they only project what the services
already record.  Every read is scoped to the JWT principal's ``doctor_id`` so a
doctor can never see another tenant's audit trail.

Owner: Vinay (scope ``eval``); mounted in ``api/app.py`` by Naresh.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from careline.adapters.auth.principals import DoctorPrincipal
from careline.api.deps import get_current_doctor
from careline.api.dto.observability import (
    AuditCallOut,
    AuditLogOut,
    AuditTurnOut,
    EscalationsOut,
    EvalRunOut,
    EvalScenarioOut,
)
from careline.services.audit_service import (
    AuditCallRecord,
    AuditService,
    AuditTurnRecord,
)
from careline.services.eval_rerun import rerun_offline_eval

router = APIRouter(tags=["observability"])


def _turn_out(turn: AuditTurnRecord) -> AuditTurnOut:
    return AuditTurnOut(
        turn_id=turn.turn_id,
        call_id=turn.call_id,
        patient_id=turn.patient_id,
        logged_at=turn.logged_at,
        verdict=turn.verdict,
        question=turn.question,
        answer_text=turn.answer_text,
        escalation_reason=turn.escalation_reason,
        confidence=turn.confidence,
        risk=turn.risk,
        trace_steps=turn.trace_steps,
        redacted=turn.redacted,
    )


def _call_out(call: AuditCallRecord) -> AuditCallOut:
    return AuditCallOut(
        call_id=call.call_id,
        patient_id=call.patient_id,
        started_at=call.started_at,
        ended_at=call.ended_at,
        turn_count=call.turn_count,
        final_verdict=call.final_verdict,
        escalated=call.escalated,
        redacted=call.redacted,
    )


@router.get("/audit", response_model=AuditLogOut)
async def get_audit(
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> AuditLogOut:
    """Doctor-scoped audit trail — every call and turn for this doctor."""
    audit: AuditService = request.app.state.audit
    calls = sorted(
        audit.calls_for_doctor(principal.doctor_id),
        key=lambda c: c.started_at,
        reverse=True,
    )
    turns = audit.turns_for_doctor(principal.doctor_id)
    return AuditLogOut(
        calls=[_call_out(c) for c in calls],
        turns=[_turn_out(t) for t in turns],
    )


@router.get("/escalations", response_model=EscalationsOut)
async def get_escalations(
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> EscalationsOut:
    """Doctor-scoped human-handoff queue — turns that terminated in ESCALATE."""
    audit: AuditService = request.app.state.audit
    escalations = audit.escalations_for_doctor(principal.doctor_id)
    return EscalationsOut(
        waiting=len(escalations),
        escalations=[_turn_out(t) for t in escalations],
    )


@router.get("/eval", response_model=EvalRunOut)
async def get_eval(
    request: Request,
    _principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> EvalRunOut:
    """Re-run the offline T-scenarios through the live spine and report results.

    The re-run is self-contained (fixed fixtures, heuristic backend) so it is
    safe and deterministic on every request — it does not touch the doctor's
    live audit log.
    """
    results, digest = rerun_offline_eval()
    passed = sum(1 for _, _, ok in results if ok)
    return EvalRunOut(
        passed=passed,
        total=len(results),
        digest=digest,
        scenarios=[
            EvalScenarioOut(name=name, verdict=verdict, passed=ok)
            for name, verdict, ok in results
        ],
    )
