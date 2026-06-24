"""Audit read routes and offline eval re-run (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from careline.adapters.auth.principals import DoctorPrincipal
from careline.api.deps import get_current_doctor
from careline.api.dto.audit import (
    AuditCallOut,
    AuditEventOut,
    AuditTurnOut,
    EvalRunOut,
    EvalScenarioOut,
)
from careline.services.audit_service import AuditEventKind
from careline.services.eval_rerun import rerun_offline_eval

router = APIRouter(tags=["audit"])


@router.get("/audit/turns", response_model=list[AuditTurnOut])
async def list_audit_turns(
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> list[AuditTurnOut]:
    """All question turns for the authenticated doctor, newest first."""
    audit = request.app.state.audit
    turns = [t for t in audit.turns if t.doctor_id == principal.doctor_id]
    turns.sort(key=lambda t: t.logged_at, reverse=True)
    return [AuditTurnOut.model_validate(t.model_dump()) for t in turns]


@router.get("/audit/calls", response_model=list[AuditCallOut])
async def list_audit_calls(
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> list[AuditCallOut]:
    """All calls for the authenticated doctor, newest first."""
    audit = request.app.state.audit
    calls = audit.calls_for_doctor(principal.doctor_id)
    calls.sort(key=lambda c: c.started_at, reverse=True)
    return [AuditCallOut.model_validate(c.model_dump()) for c in calls]


@router.get("/audit/events", response_model=list[AuditEventOut])
async def list_audit_events(
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> list[AuditEventOut]:
    """System events scoped to the authenticated doctor, newest first."""
    audit = request.app.state.audit
    events = [
        e
        for e in audit.events
        if e.doctor_id is None or e.doctor_id == principal.doctor_id
    ]
    events.sort(key=lambda e: e.logged_at, reverse=True)
    return [AuditEventOut.model_validate(e.model_dump()) for e in events]


@router.post("/eval/run", response_model=EvalRunOut)
async def run_eval(
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> EvalRunOut:
    """Re-run offline eval scenarios and log outcomes to audit."""
    results, digest = rerun_offline_eval(audit=request.app.state.audit)
    scenarios = [
        EvalScenarioOut(name=name, verdict=verdict, passed=passed)
        for name, verdict, passed in results
    ]
    passed_count = sum(1 for _, _, ok in results if ok)
    request.app.state.audit.log_event(
        AuditEventKind.EVAL,
        doctor_id=principal.doctor_id,
        detail="offline eval re-run via API",
        metadata={"passed": passed_count, "total": len(results)},
    )
    return EvalRunOut(
        passed=passed_count,
        total=len(results),
        digest=digest,
        scenarios=scenarios,
    )
