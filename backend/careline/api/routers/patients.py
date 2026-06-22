"""Patient read routes — tenant-scoped, no-leak (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from careline.adapters.auth.principals import DoctorPrincipal
from careline.api.deps import get_current_doctor
from careline.api.dto.patients import ErasureOut, PatientOut

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("/{patient_id}", response_model=PatientOut)
async def get_patient(
    patient_id: str,
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> PatientOut:
    """Return a patient summary under the authenticated doctor."""
    patient = await request.app.state.patient_repo.get(
        doctor_id=principal.doctor_id,
        patient_id=patient_id,
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="not found")
    return PatientOut(
        patient_id=patient.patient_id,
        doctor_id=patient.doctor_id,
        fact_count=len(patient.facts),
    )


@router.delete("/{patient_id}/data", response_model=ErasureOut)
async def erase_patient_data(
    patient_id: str,
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> ErasureOut:
    """DPDP right-to-erasure — null clinical data across all layers."""
    result = await request.app.state.dpdp_svc.erase(
        doctor_id=principal.doctor_id,
        patient_id=patient_id,
    )
    return ErasureOut(
        patient_id=result.patient_id,
        layer1_nulled=result.layer1_nulled,
        layer2_dropped=result.layer2_dropped,
        audit_redacted=result.audit_redacted,
    )
