"""ApprovalService tests (NR-4).

Pins the one-tap HITL pipeline: consent and draft guards, fact stamping,
Layer-1 supersession, Layer-2 reindex, consultation promotion, and audit.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from careline.adapters.memory.local import LocalMemoryProvider
from careline.adapters.mongo.supersession import plan_supersession
from careline.domain.enums import FactKind
from careline.domain.model.consultation import Consultation
from careline.domain.model.fact import Fact, Instruction, Medication
from careline.domain.model.patient import Patient, PatientIdentity, ValidSlice
from careline.domain.model.temporal import Validity
from careline.domain.ports.memory import MemoryProvider
from careline.domain.ports.repositories import ConsultationRepository, PatientRepository
from careline.services.approval_service import (
    AlreadyApprovedError,
    ApprovalService,
    NoFactsError,
)
from careline.services.audit_service import AuditEventKind, AuditService
from careline.services.consultation_service import (
    ConsentViolation,
    ConsultationNotFound,
    ConsultationService,
)
from careline.services.extraction_service import ExtractionService, HeuristicExtractor

_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
_PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)
_PURPOSE = "post-consultation follow-up answering"
_DR_A = "dr-A"
_DR_B = "dr-B"
_PATIENT = "patient-A"
_TRANSCRIPT = "Prescribed Paracetamol 500mg twice daily."


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


class _InMemoryPatientRepository(PatientRepository):
    """Offline double — apply_facts uses the same supersession plan as Mongo."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], Patient] = {}
        self._identities: dict[tuple[str, str], PatientIdentity] = {}

    def _key(self, *, doctor_id: str, patient_id: str) -> tuple[str, str]:
        return (doctor_id, patient_id)

    def _caller_key(self, *, doctor_id: str, caller_id: str) -> tuple[str, str]:
        return (doctor_id, caller_id)

    async def get(self, *, doctor_id: str, patient_id: str) -> Patient | None:
        return self._store.get(self._key(doctor_id=doctor_id, patient_id=patient_id))

    async def exists(self, *, doctor_id: str, patient_id: str) -> bool:
        return self._key(doctor_id=doctor_id, patient_id=patient_id) in self._store

    async def valid_slice(
        self, *, doctor_id: str, patient_id: str, now: datetime
    ) -> ValidSlice:
        patient = await self.get(doctor_id=doctor_id, patient_id=patient_id)
        if patient is None:
            return ValidSlice(as_of=now, facts=())
        return patient.valid_slice(now)

    async def history(
        self, *, doctor_id: str, patient_id: str, now: datetime
    ) -> tuple[Fact, ...]:
        patient = await self.get(doctor_id=doctor_id, patient_id=patient_id)
        if patient is None:
            return ()
        return patient.history(now)

    async def add_facts(
        self, *, doctor_id: str, patient_id: str, facts: tuple[Fact, ...]
    ) -> None:
        if not facts:
            return
        patient = await self.get(doctor_id=doctor_id, patient_id=patient_id)
        if patient is None:
            patient = Patient(patient_id=patient_id, doctor_id=doctor_id, facts=())
        for fact in facts:
            patient = patient.with_fact(fact)
        self._store[self._key(doctor_id=doctor_id, patient_id=patient_id)] = patient

    async def apply_facts(
        self,
        *,
        doctor_id: str,
        patient_id: str,
        facts: tuple[Fact, ...],
        now: datetime,
    ) -> tuple[Fact, ...]:
        if not facts:
            return ()
        patient = await self.get(doctor_id=doctor_id, patient_id=patient_id)
        if patient is None:
            patient = Patient(patient_id=patient_id, doctor_id=doctor_id, facts=())
        current = patient.valid_slice(now)
        plan = plan_supersession(current=current.facts, incoming=facts, now=now)
        close_ids = set(plan.to_close)
        retired = tuple(f for f in current.facts if f.id in close_ids)
        updated: list[Fact] = []
        for f in patient.facts:
            if f.id in close_ids:
                updated.append(f.supersede(now))
            else:
                updated.append(f)
        updated.extend(plan.to_insert)
        self._store[self._key(doctor_id=doctor_id, patient_id=patient_id)] = Patient(
            patient_id=patient_id,
            doctor_id=doctor_id,
            facts=tuple(updated),
        )
        return retired

    async def soft_delete(self, *, doctor_id: str, patient_id: str) -> int:
        key = self._key(doctor_id=doctor_id, patient_id=patient_id)
        if key not in self._store:
            return 0
        del self._store[key]
        return 1

    async def find_by_caller(
        self, *, doctor_id: str, caller_id: str
    ) -> PatientIdentity | None:
        return self._identities.get(self._caller_key(doctor_id=doctor_id, caller_id=caller_id))

    async def upsert_identity(self, *, identity: PatientIdentity) -> None:
        self._identities[
            self._caller_key(doctor_id=identity.doctor_id, caller_id=identity.caller_id)
        ] = identity


class _MemorySpy(LocalMemoryProvider):
    """Records ``index`` calls for Layer-2 wiring assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.index_calls: list[tuple[str, str, ValidSlice]] = []

    async def index(
        self, *, doctor_id: str, patient_id: str, slice: ValidSlice
    ) -> None:
        self.index_calls.append((doctor_id, patient_id, slice))
        await super().index(doctor_id=doctor_id, patient_id=patient_id, slice=slice)


def _run(coro):
    return asyncio.run(coro)


def _draft_instruction() -> Instruction:
    return Instruction(
        id="fact-001",
        kind=FactKind.INSTRUCTION,
        validity=Validity(effective_from=_NOW),
        summary="rest for one week",
        text="rest for one week",
    )


def _stack(
    *,
    audit: AuditService | None = None,
    memory: MemoryProvider | None = None,
    patient_repo: PatientRepository | None = None,
) -> tuple[ApprovalService, ConsultationService, PatientRepository, _MemorySpy | MemoryProvider]:
    consultation_repo = _InMemoryConsultationRepository()
    consultation_svc = ConsultationService(repo=consultation_repo, audit=audit)
    repo = patient_repo or _InMemoryPatientRepository()
    memory_impl: _MemorySpy | MemoryProvider = memory if memory is not None else _MemorySpy()
    approval_svc = ApprovalService(
        consultation_svc=consultation_svc,
        patient_repo=repo,
        memory=memory_impl,
        audit=audit,
    )
    return approval_svc, consultation_svc, repo, memory_impl


async def _draft_with_facts(
    consultation_svc: ConsultationService,
    *,
    facts: tuple[Fact, ...] | None = None,
) -> Consultation:
    c = await consultation_svc.create(
        doctor_id=_DR_A,
        patient_id=_PATIENT,
        transcript=_TRANSCRIPT,
        now=_NOW,
    )
    await consultation_svc.stamp_consent(
        doctor_id=_DR_A,
        consultation_id=c.consultation_id,
        purpose=_PURPOSE,
        now=_NOW,
    )
    if facts is not None:
        return await consultation_svc.attach_facts(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            facts=facts,
            now=_NOW,
        )
    reloaded = await consultation_svc.get(
        doctor_id=_DR_A, consultation_id=c.consultation_id
    )
    assert reloaded is not None
    return reloaded


def test_approve_without_consent_raises_consent_violation():
    consultation_repo = _InMemoryConsultationRepository()
    consultation_svc = ConsultationService(repo=consultation_repo)
    patient_repo = _InMemoryPatientRepository()
    approval_svc = ApprovalService(
        consultation_svc=consultation_svc,
        patient_repo=patient_repo,
        memory=_MemorySpy(),
    )
    c = _run(
        consultation_svc.create(
            doctor_id=_DR_A,
            patient_id=_PATIENT,
            transcript=_TRANSCRIPT,
            now=_NOW,
        )
    )
    c_with_facts = c.with_facts((_draft_instruction(),))
    _run(consultation_repo.save(c_with_facts))
    with pytest.raises(ConsentViolation):
        _run(
            approval_svc.approve(
                doctor_id=_DR_A,
                consultation_id=c.consultation_id,
                now=_NOW,
            )
        )


def test_approve_already_approved_raises_already_approved_error():
    approval_svc, consultation_svc, _, _ = _stack()
    c = _run(_draft_with_facts(consultation_svc, facts=(_draft_instruction(),)))
    _run(
        approval_svc.approve(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    with pytest.raises(AlreadyApprovedError):
        _run(
            approval_svc.approve(
                doctor_id=_DR_A,
                consultation_id=c.consultation_id,
                now=_NOW,
            )
        )


def test_approve_with_no_facts_raises_no_facts_error():
    approval_svc, consultation_svc, _, _ = _stack()
    c = _run(_draft_with_facts(consultation_svc))
    with pytest.raises(NoFactsError):
        _run(
            approval_svc.approve(
                doctor_id=_DR_A,
                consultation_id=c.consultation_id,
                now=_NOW,
            )
        )


def test_approve_stamps_all_facts_approved_by_doctor():
    approval_svc, consultation_svc, patient_repo, _ = _stack()
    c = _run(_draft_with_facts(consultation_svc, facts=(_draft_instruction(),)))
    _run(
        approval_svc.approve(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    slice = _run(
        patient_repo.valid_slice(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW)
    )
    assert len(slice.facts) == 1
    assert slice.facts[0].approved_by == _DR_A
    assert slice.facts[0].approved_at == _NOW


def test_approve_calls_apply_facts_on_patient_repo():
    approval_svc, consultation_svc, patient_repo, _ = _stack()
    c = _run(_draft_with_facts(consultation_svc, facts=(_draft_instruction(),)))
    _run(
        approval_svc.approve(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    assert _run(patient_repo.exists(doctor_id=_DR_A, patient_id=_PATIENT))
    slice = _run(
        patient_repo.valid_slice(doctor_id=_DR_A, patient_id=_PATIENT, now=_NOW)
    )
    assert slice.facts[0].id == "fact-001"


def test_approve_indexes_layer2_memory_after_apply_facts():
    approval_svc, consultation_svc, _, memory = _stack()
    c = _run(_draft_with_facts(consultation_svc, facts=(_draft_instruction(),)))
    _run(
        approval_svc.approve(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    assert isinstance(memory, _MemorySpy)
    assert len(memory.index_calls) == 1
    _, patient_id, indexed_slice = memory.index_calls[0]
    assert patient_id == _PATIENT
    assert not indexed_slice.is_empty
    hits = _run(
        memory.retrieve(
            doctor_id=_DR_A,
            patient_id=_PATIENT,
            query="rest week",
            k=5,
        )
    )
    assert len(hits) >= 1


def test_approve_marks_consultation_status_approved():
    approval_svc, consultation_svc, _, _ = _stack()
    c = _run(_draft_with_facts(consultation_svc, facts=(_draft_instruction(),)))
    result = _run(
        approval_svc.approve(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    assert result.consultation.status == "approved"
    assert result.consultation.is_approved is True
    assert all(f.is_approved for f in result.consultation.facts)


def test_approve_returns_correct_applied_and_retired_counts():
    approval_svc, consultation_svc, patient_repo, _ = _stack()
    old_med = Medication(
        id="m-old",
        validity=Validity(effective_from=_PAST),
        summary="Paracetamol",
        name="Paracetamol",
        approved_by=_DR_A,
        approved_at=_PAST,
    )
    _run(patient_repo.add_facts(doctor_id=_DR_A, patient_id=_PATIENT, facts=(old_med,)))
    extraction_svc = ExtractionService(
        extractor=HeuristicExtractor(),
        consultation_svc=consultation_svc,
    )
    c = _run(_draft_with_facts(consultation_svc))
    c = _run(
        extraction_svc.extract(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    result = _run(
        approval_svc.approve(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    assert result.applied_facts >= 1
    assert result.retired_facts == 1


def test_approve_supersession_retires_old_medication():
    approval_svc, consultation_svc, patient_repo, _ = _stack()
    old_med = Medication(
        id="m-old",
        validity=Validity(effective_from=_PAST),
        summary="Paracetamol",
        name="Paracetamol",
        approved_by=_DR_A,
        approved_at=_PAST,
    )
    _run(patient_repo.add_facts(doctor_id=_DR_A, patient_id=_PATIENT, facts=(old_med,)))
    new_med = Medication(
        id="m-new",
        validity=Validity(effective_from=_NOW),
        summary="Paracetamol 500mg",
        name="Paracetamol",
    )
    c = _run(_draft_with_facts(consultation_svc, facts=(new_med,)))
    _run(
        approval_svc.approve(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    patient = _run(patient_repo.get(doctor_id=_DR_A, patient_id=_PATIENT))
    assert patient is not None
    old = next(f for f in patient.facts if f.id == "m-old")
    assert old.validity.superseded_at == _NOW
    slice = patient.valid_slice(_NOW)
    current_meds = slice.of_kind(FactKind.MEDICATION)
    assert len(current_meds) == 1
    assert current_meds[0].id == "m-new"


def test_approve_emits_audit_system_event_with_fact_count():
    audit = AuditService()
    approval_svc, consultation_svc, _, _ = _stack(audit=audit)
    c = _run(_draft_with_facts(consultation_svc, facts=(_draft_instruction(),)))
    _run(
        approval_svc.approve(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    system_events = [e for e in audit.events if e.kind is AuditEventKind.SYSTEM]
    assert len(system_events) == 1
    assert "1 fact" in (system_events[0].detail or "").lower()


def test_approve_cross_tenant_blocked():
    approval_svc, consultation_svc, _, _ = _stack()
    c = _run(_draft_with_facts(consultation_svc, facts=(_draft_instruction(),)))
    with pytest.raises(ConsultationNotFound):
        _run(
            approval_svc.approve(
                doctor_id=_DR_B,
                consultation_id=c.consultation_id,
                now=_NOW,
            )
        )
