"""Patient read routes — tenant-scoped, no-leak (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from careline.adapters.auth.principals import DoctorPrincipal
from careline.api.deps import get_current_doctor
from careline.api.dto.patients import (
    ErasureOut,
    FactOut,
    PatientOut,
    PatientRecordOut,
    PatientRegisterIn,
)
from careline.domain.model.patient import PatientIdentity
from careline.services.patient_lookup_service import hash_pin

router = APIRouter(prefix="/patients", tags=["patients"])


@router.post("", response_model=PatientOut, status_code=status.HTTP_201_CREATED)
async def register_patient(
    body: PatientRegisterIn,
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> PatientOut:
    """Register caller-id + PIN for a patient under the authenticated doctor."""
    settings = request.app.state.settings
    pin_hmac = hash_pin(pin=body.pin, secret=settings.pin_hmac_secret)
    identity = PatientIdentity(
        patient_id=body.patient_id,
        doctor_id=principal.doctor_id,
        caller_id=body.caller_id,
        pin_hmac=pin_hmac,
    )
    await request.app.state.patient_repo.upsert_identity(identity=identity)
    patient = await request.app.state.patient_repo.get(
        doctor_id=principal.doctor_id,
        patient_id=body.patient_id,
    )
    fact_count = len(patient.facts) if patient is not None else 0
    return PatientOut(
        patient_id=body.patient_id,
        doctor_id=principal.doctor_id,
        fact_count=fact_count,
    )


@router.get("", response_model=list[PatientOut])
async def list_patients(
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> list[PatientOut]:
    """Every patient registered under the authenticated doctor.

    Tenant-scoped: only this doctor's patients are ever returned. Each carries its
    approved-fact count so the UI can show who is answerable (count > 0) vs. merely
    registered. Powers the console patient picker and the patients-page list.
    """
    rows = await request.app.state.patient_repo.list_for_doctor(
        doctor_id=principal.doctor_id
    )
    return [
        PatientOut(
            patient_id=pid,
            doctor_id=principal.doctor_id,
            fact_count=fact_count,
        )
        for pid, fact_count in rows
    ]


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


@router.get("/{patient_id}/record", response_model=PatientRecordOut)
async def get_patient_record(
    patient_id: str,
    request: Request,
    principal: Annotated[DoctorPrincipal, Depends(get_current_doctor)],
) -> PatientRecordOut:
    """Return the patient's valid slice (current facts) + superseded history.

    Tenant-scoped to the authenticated doctor; a cross-tenant or unknown patient is
    a 404, never another doctor's record. The repository pushes the half-open
    validity + approval predicate down, so ``current`` never contains a superseded
    or unapproved fact.
    """
    repo = request.app.state.patient_repo
    patient = await repo.get(doctor_id=principal.doctor_id, patient_id=patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="not found")

    now = datetime.now(timezone.utc)
    valid = await repo.valid_slice(
        doctor_id=principal.doctor_id, patient_id=patient_id, now=now
    )
    retired = await repo.history(
        doctor_id=principal.doctor_id, patient_id=patient_id, now=now
    )
    return PatientRecordOut(
        patient_id=patient.patient_id,
        doctor_id=patient.doctor_id,
        as_of=now,
        current=[FactOut.from_fact(f, current=True) for f in valid.facts],
        history=[FactOut.from_fact(f, current=False) for f in retired],
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
