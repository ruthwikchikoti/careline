"""ConsultationService tests (NR-2).

Pins the DPDP consent gate: no processing without active consent, consent
stamping is auditable, and cross-tenant reads resolve to not-found.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from careline.domain.enums import FactKind
from careline.domain.model.consultation import Consultation
from careline.domain.model.fact import Instruction
from careline.domain.model.temporal import Validity
from careline.domain.ports.repositories import ConsultationRepository
from careline.services.audit_service import AuditEventKind, AuditService
from careline.services.consultation_service import (
    ConsentViolation,
    ConsultationNotFound,
    ConsultationService,
)

_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
_PURPOSE = "post-consultation follow-up answering"
_DR_A = "dr-A"
_DR_B = "dr-B"
_PATIENT = "patient-A"


class _InMemoryConsultationRepository(ConsultationRepository):
    """Offline double — tenant-scoped like the real Mongo adapter."""

    def __init__(self) -> None:
        self._store: dict[str, Consultation] = {}

    async def get(self, *, doctor_id: str, consultation_id: str) -> Consultation | None:
        c = self._store.get(consultation_id)
        if c is None or c.doctor_id != doctor_id:
            return None
        return c

    async def save(self, consultation: Consultation) -> None:
        self._store[consultation.consultation_id] = consultation

    async def list_for_patient(
        self, *, doctor_id: str, patient_id: str
    ) -> tuple[Consultation, ...]:
        return tuple(
            c
            for c in self._store.values()
            if c.doctor_id == doctor_id and c.patient_id == patient_id
        )

    async def list_for_doctor(
        self, *, doctor_id: str, limit: int = 50
    ) -> tuple[Consultation, ...]:
        results = sorted(
            (c for c in self._store.values() if c.doctor_id == doctor_id),
            key=lambda c: c.created_at,
            reverse=True,
        )
        return tuple(results[:limit])


def _service(*, audit: AuditService | None = None) -> ConsultationService:
    return ConsultationService(repo=_InMemoryConsultationRepository(), audit=audit)


def _run(coro):
    return asyncio.run(coro)


def test_create_returns_draft_without_consent():
    svc = _service()
    c = _run(
        svc.create(
            doctor_id=_DR_A,
            patient_id=_PATIENT,
            transcript="patient reports mild pain",
            now=_NOW,
        )
    )
    assert c.status == "draft"
    assert c.consent is None
    assert c.is_processable is False


def test_stamp_consent_makes_consultation_processable():
    svc = _service()
    c = _run(svc.create(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW))
    stamped = _run(
        svc.stamp_consent(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            purpose=_PURPOSE,
            now=_NOW,
        )
    )
    assert stamped.is_processable is True
    assert stamped.consent is not None
    assert stamped.consent.is_active is True


def test_stamp_consent_emits_audit_consent_event():
    audit = AuditService()
    svc = _service(audit=audit)
    c = _run(svc.create(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW))
    _run(
        svc.stamp_consent(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            purpose=_PURPOSE,
            now=_NOW,
        )
    )
    assert len(audit.events) == 1
    assert audit.events[0].kind is AuditEventKind.CONSENT


def test_approve_without_consent_raises():
    svc = _service()
    c = _run(svc.create(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW))
    with pytest.raises(ConsentViolation):
        _run(svc.approve(doctor_id=_DR_A, consultation_id=c.consultation_id, now=_NOW))


def test_approve_after_stamp_consent_succeeds():
    svc = _service()
    c = _run(svc.create(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW))
    _run(
        svc.stamp_consent(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            purpose=_PURPOSE,
            now=_NOW,
        )
    )
    approved = _run(
        svc.approve(doctor_id=_DR_A, consultation_id=c.consultation_id, now=_NOW)
    )
    assert approved.status == "approved"
    assert approved.is_approved is True


def test_withdraw_consent_blocks_approval():
    svc = _service()
    c = _run(svc.create(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW))
    _run(
        svc.stamp_consent(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            purpose=_PURPOSE,
            now=_NOW,
        )
    )
    _run(
        svc.withdraw_consent(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=datetime(2026, 6, 22, tzinfo=timezone.utc),
        )
    )
    with pytest.raises(ConsentViolation):
        _run(svc.approve(doctor_id=_DR_A, consultation_id=c.consultation_id, now=_NOW))


def test_withdraw_inactive_consent_raises():
    svc = _service()
    c = _run(svc.create(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW))
    with pytest.raises(ConsentViolation):
        _run(
            svc.withdraw_consent(
                doctor_id=_DR_A,
                consultation_id=c.consultation_id,
                now=_NOW,
            )
        )


def test_get_returns_none_for_wrong_doctor():
    svc = _service()
    c = _run(svc.create(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW))
    assert _run(svc.get(doctor_id=_DR_B, consultation_id=c.consultation_id)) is None


def test_list_for_patient_is_tenant_scoped():
    svc = _service()
    c_a = _run(svc.create(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW))
    _run(svc.create(doctor_id=_DR_B, patient_id=_PATIENT, now=_NOW))
    listed = _run(svc.list_for_patient(doctor_id=_DR_A, patient_id=_PATIENT))
    assert len(listed) == 1
    assert listed[0].consultation_id == c_a.consultation_id


def test_list_for_doctor_is_tenant_scoped_and_newest_first():
    svc = _service()
    older = _run(
        svc.create(
            doctor_id=_DR_A,
            patient_id="patient-old",
            consultation_id="c-old",
            now=datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc),
        )
    )
    newer = _run(
        svc.create(
            doctor_id=_DR_A,
            patient_id="patient-new",
            consultation_id="c-new",
            now=datetime(2026, 6, 21, 10, 0, tzinfo=timezone.utc),
        )
    )
    _run(svc.create(doctor_id=_DR_B, patient_id=_PATIENT, now=_NOW))
    listed = _run(svc.list(doctor_id=_DR_A))
    assert len(listed) == 2
    assert listed[0].consultation_id == newer.consultation_id
    assert listed[1].consultation_id == older.consultation_id


def test_stamp_on_nonexistent_consultation_raises_not_found():
    svc = _service()
    with pytest.raises(ConsultationNotFound):
        _run(
            svc.stamp_consent(
                doctor_id=_DR_A,
                consultation_id="missing",
                purpose=_PURPOSE,
                now=_NOW,
            )
        )


def _draft_fact() -> Instruction:
    return Instruction(
        id="fact-001",
        kind=FactKind.INSTRUCTION,
        validity=Validity(effective_from=_NOW),
        summary="rest for one week",
        text="rest for one week",
    )


def test_attach_facts_requires_active_consent():
    svc = _service()
    c = _run(svc.create(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW))
    with pytest.raises(ConsentViolation):
        _run(
            svc.attach_facts(
                doctor_id=_DR_A,
                consultation_id=c.consultation_id,
                facts=(_draft_fact(),),
                now=_NOW,
            )
        )


def test_attach_facts_stores_on_draft():
    svc = _service()
    c = _run(svc.create(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW))
    _run(
        svc.stamp_consent(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            purpose=_PURPOSE,
            now=_NOW,
        )
    )
    updated = _run(
        svc.attach_facts(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            facts=(_draft_fact(),),
            now=_NOW,
        )
    )
    assert updated.status == "draft"
    assert len(updated.facts) == 1
    assert updated.facts[0].id == "fact-001"
