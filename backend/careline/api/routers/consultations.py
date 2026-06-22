"""Consultation Track A routes (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from careline.adapters.auth.principals import DoctorPrincipal
from careline.api.deps import get_current_doctor
from careline.api.dto.consultations import (
    ApprovalOut,
    ConsentIn,
    ConsultationCreateIn,
    ConsultationOut,
    ExtractOut,
)
from careline.domain.model.consultation import Consultation

router = APIRouter(prefix="/consultations", tags=["consultations"])


def _consultation_out(consultation: Consultation) -> ConsultationOut:
    return ConsultationOut(
        consultation_id=consultation.consultation_id,
        doctor_id=consultation.doctor_id,
        patient_id=consultation.patient_id,
        status=consultation.status,
        created_at=consultation.created_at,
        fact_count=len(consultation.facts),
    )


@router.post("", response_model=ConsultationOut, status_code=status.HTTP_201_CREATED)
async def create_consultation(
    body: ConsultationCreateIn,
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> ConsultationOut:
    """Open a draft consultation for one patient."""
    consultation = await request.app.state.consultation_svc.create(
        doctor_id=principal.doctor_id,
        patient_id=body.patient_id,
        transcript=body.transcript,
    )
    return _consultation_out(consultation)


@router.get("", response_model=list[ConsultationOut])
async def list_consultations(
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> list[ConsultationOut]:
    """All consultations for the authenticated doctor, newest first."""
    consultations = await request.app.state.consultation_svc.list(
        doctor_id=principal.doctor_id
    )
    return [_consultation_out(c) for c in consultations]


@router.get("/{consultation_id}", response_model=ConsultationOut)
async def get_consultation(
    consultation_id: str,
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> ConsultationOut:
    """Fetch one consultation — wrong tenant returns generic 404."""
    consultation = await request.app.state.consultation_svc.get(
        doctor_id=principal.doctor_id,
        consultation_id=consultation_id,
    )
    if consultation is None:
        raise HTTPException(status_code=404, detail="not found")
    return _consultation_out(consultation)


@router.post("/{consultation_id}/consent", response_model=ConsultationOut)
async def stamp_consent(
    consultation_id: str,
    body: ConsentIn,
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> ConsultationOut:
    """Stamp explicit consent before extraction or approval."""
    consultation = await request.app.state.consultation_svc.stamp_consent(
        doctor_id=principal.doctor_id,
        consultation_id=consultation_id,
        purpose=body.purpose,
    )
    return _consultation_out(consultation)


@router.post("/{consultation_id}/extract", response_model=ExtractOut)
async def extract_facts(
    consultation_id: str,
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> ExtractOut:
    """Run extraction and attach drafted facts to the consultation."""
    consultation = await request.app.state.extraction_svc.extract(
        doctor_id=principal.doctor_id,
        consultation_id=consultation_id,
    )
    return ExtractOut(
        consultation_id=consultation.consultation_id,
        fact_count=len(consultation.facts),
        status=consultation.status,
    )


@router.post("/{consultation_id}/approve", response_model=ApprovalOut)
async def approve_consultation(
    consultation_id: str,
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> ApprovalOut:
    """One-tap HITL approval — promote drafted facts into the live record."""
    result = await request.app.state.approval_svc.approve(
        doctor_id=principal.doctor_id,
        consultation_id=consultation_id,
    )
    return ApprovalOut(
        consultation_id=result.consultation.consultation_id,
        status=result.consultation.status,
        applied_facts=result.applied_facts,
        retired_facts=result.retired_facts,
    )
