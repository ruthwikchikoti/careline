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

from fastapi import APIRouter, Depends, HTTPException, Request

from careline.adapters.auth.principals import DoctorPrincipal
from careline.api.deps import get_current_doctor
from careline.api.dto.observability import (
    AuditCallOut,
    AuditEventOut,
    AuditLogOut,
    AuditTurnOut,
    EscalationGroupOut,
    EscalationResolveIn,
    EscalationResolveOut,
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


def _turn_out(turn: AuditTurnRecord, audit: AuditService | None = None) -> AuditTurnOut:
    resolution = audit.resolution_for(turn.turn_id) if audit is not None else None
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
        resolved=resolution is not None,
        reply=resolution.reply_text if resolution else None,
        resolved_at=resolution.resolved_at if resolution else None,
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
        turns=[_turn_out(t, audit) for t in turns],
    )


@router.get("/audit/events", response_model=list[AuditEventOut])
async def get_audit_events(
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> list[AuditEventOut]:
    """Doctor-scoped audit events (consent, erasure, eval, system), newest first.

    System events with no doctor attribution (``doctor_id is None``) are visible
    to every doctor; doctor-stamped events are tenant-scoped.
    """
    audit: AuditService = request.app.state.audit
    events = [
        e
        for e in audit.events
        if e.doctor_id is None or e.doctor_id == principal.doctor_id
    ]
    events.sort(key=lambda e: e.logged_at, reverse=True)
    return [
        AuditEventOut(
            event_id=e.event_id,
            kind=e.kind.value,
            logged_at=e.logged_at,
            patient_id=e.patient_id,
            detail=e.detail,
            metadata=e.metadata,
        )
        for e in events
    ]


@router.get("/escalations", response_model=EscalationsOut)
async def get_escalations(
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> EscalationsOut:
    """Doctor-scoped human-handoff queue — ESCALATE turns, grouped by patient.

    The flat list (newest first) is preserved; ``groups`` bundles those same turns
    per patient so the doctor triages by *who* is waiting rather than scanning
    row-by-row. Patients are ordered by their most recent escalation.
    """
    audit: AuditService = request.app.state.audit
    escalations = audit.escalations_for_doctor(principal.doctor_id)
    flat = [_turn_out(t, audit) for t in escalations]

    # Group preserving the newest-first order within each patient. Resolved turns
    # stay visible (marked) but don't count toward "waiting".
    grouped: dict[str, list[AuditTurnOut]] = {}
    for turn in flat:
        grouped.setdefault(turn.patient_id, []).append(turn)
    groups = [
        EscalationGroupOut(
            patient_id=pid,
            count=sum(1 for t in turns if not t.resolved),
            latest_at=turns[0].logged_at,  # flat is newest-first → first is latest
            escalations=turns,
        )
        for pid, turns in grouped.items()
    ]
    groups.sort(key=lambda g: g.latest_at, reverse=True)

    waiting = sum(1 for t in flat if not t.resolved)
    return EscalationsOut(
        waiting=waiting,
        patients_waiting=sum(1 for g in groups if g.count > 0),
        groups=groups,
        escalations=flat,
    )


@router.post("/escalations/{turn_id}/resolve", response_model=EscalationResolveOut)
async def resolve_escalation(
    turn_id: str,
    body: EscalationResolveIn,
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> EscalationResolveOut:
    """Close an escalated turn with the doctor's reply (the human-in-the-loop answer).

    Tenant-scoped: a doctor can only resolve escalations raised under their own
    account. The reply is persisted and surfaced back to the patient (their
    "answered" view), closing the escalation loop.
    """
    audit: AuditService = request.app.state.audit
    turn = next(
        (t for t in audit.escalations_for_doctor(principal.doctor_id) if t.turn_id == turn_id),
        None,
    )
    if turn is None:
        raise HTTPException(status_code=404, detail="escalation not found")
    record = audit.resolve_escalation(
        turn_id=turn_id, reply_text=body.reply, resolved_by=principal.doctor_id
    )
    if record is None:  # pragma: no cover - turn existed above
        raise HTTPException(status_code=404, detail="escalation not found")
    return EscalationResolveOut(
        turn_id=record.turn_id,
        patient_id=record.patient_id,
        reply=record.reply_text,
        resolved_at=record.resolved_at,
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
