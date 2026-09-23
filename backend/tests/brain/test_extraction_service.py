"""ExtractionService tests (NR-3).

Pins the Track A extraction pipeline: consent gate before LLM, no partial
persist on extractor failure, and drafted facts remain unapproved until HITL.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from careline.domain.model.consultation import Consultation
from careline.domain.ports.extraction import Extractor
from careline.domain.ports.reasoning import ReasonerUnavailable
from careline.domain.ports.repositories import ConsultationRepository
from careline.services.audit_service import AuditEventKind, AuditService
from careline.services.consultation_service import (
    ConsentViolation,
    ConsultationNotFound,
    ConsultationService,
)
from careline.services.extraction_service import (
    ExtractedRecord,
    ExtractionService,
    HeuristicExtractor,
    NoTranscriptError,
)

_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
_PURPOSE = "post-consultation follow-up answering"
_DR_A = "dr-A"
_DR_B = "dr-B"
_PATIENT = "patient-A"
_TRANSCRIPT = (
    "Prescribed Paracetamol 500mg twice daily. "
    "Patient should rest for one week post surgery."
)


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


class _FailingExtractor(Extractor):
    """Always fails closed — used to pin the no-partial-persist invariant."""

    def extract(
        self,
        *,
        transcript: str,
        consultation_id: str,
        now: datetime,
    ) -> ExtractedRecord:
        raise ReasonerUnavailable("extractor unavailable in test")


def _run(coro):
    return asyncio.run(coro)


def _consultation_svc(*, audit: AuditService | None = None) -> ConsultationService:
    return ConsultationService(repo=_InMemoryConsultationRepository(), audit=audit)


def _extraction_svc(
    *,
    extractor: Extractor | None = None,
    audit: AuditService | None = None,
) -> tuple[ExtractionService, ConsultationService]:
    consultation_svc = _consultation_svc(audit=audit)
    extraction_svc = ExtractionService(
        extractor=extractor or HeuristicExtractor(),
        consultation_svc=consultation_svc,
        audit=audit,
    )
    return extraction_svc, consultation_svc


async def _consented_consultation(
    consultation_svc: ConsultationService,
    *,
    transcript: str | None = _TRANSCRIPT,
) -> Consultation:
    c = await consultation_svc.create(
        doctor_id=_DR_A,
        patient_id=_PATIENT,
        transcript=transcript,
        now=_NOW,
    )
    return await consultation_svc.stamp_consent(
        doctor_id=_DR_A,
        consultation_id=c.consultation_id,
        purpose=_PURPOSE,
        now=_NOW,
    )


def test_extract_without_transcript_raises_no_transcript_error():
    extraction_svc, consultation_svc = _extraction_svc()
    c = _run(_consented_consultation(consultation_svc, transcript=None))
    with pytest.raises(NoTranscriptError):
        _run(
            extraction_svc.extract(
                doctor_id=_DR_A,
                consultation_id=c.consultation_id,
                now=_NOW,
            )
        )


def test_extract_without_consent_raises_consent_violation():
    extraction_svc, consultation_svc = _extraction_svc()
    c = _run(
        consultation_svc.create(
            doctor_id=_DR_A,
            patient_id=_PATIENT,
            transcript=_TRANSCRIPT,
            now=_NOW,
        )
    )
    with pytest.raises(ConsentViolation):
        _run(
            extraction_svc.extract(
                doctor_id=_DR_A,
                consultation_id=c.consultation_id,
                now=_NOW,
            )
        )


def test_extract_success_attaches_facts_to_draft():
    extraction_svc, consultation_svc = _extraction_svc()
    c = _run(_consented_consultation(consultation_svc))
    updated = _run(
        extraction_svc.extract(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    assert updated.status == "draft"
    assert len(updated.facts) >= 1


def test_extracted_facts_are_unapproved():
    extraction_svc, consultation_svc = _extraction_svc()
    c = _run(_consented_consultation(consultation_svc))
    updated = _run(
        extraction_svc.extract(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    assert all(not f.is_approved for f in updated.facts)
    assert all(f.approved_by is None for f in updated.facts)


def test_extract_on_nonexistent_consultation_raises_not_found():
    extraction_svc, _ = _extraction_svc()
    with pytest.raises(ConsultationNotFound):
        _run(
            extraction_svc.extract(
                doctor_id=_DR_A,
                consultation_id="missing",
                now=_NOW,
            )
        )


def test_extractor_unavailable_does_not_persist_partial_state():
    extraction_svc, consultation_svc = _extraction_svc(extractor=_FailingExtractor())
    c = _run(_consented_consultation(consultation_svc))
    with pytest.raises(ReasonerUnavailable):
        _run(
            extraction_svc.extract(
                doctor_id=_DR_A,
                consultation_id=c.consultation_id,
                now=_NOW,
            )
        )
    reloaded = _run(
        consultation_svc.get(doctor_id=_DR_A, consultation_id=c.consultation_id)
    )
    assert reloaded is not None
    assert reloaded.facts == ()


def test_extract_emits_audit_event():
    audit = AuditService()
    extraction_svc, consultation_svc = _extraction_svc(audit=audit)
    c = _run(_consented_consultation(consultation_svc))
    _run(
        extraction_svc.extract(
            doctor_id=_DR_A,
            consultation_id=c.consultation_id,
            now=_NOW,
        )
    )
    system_events = [e for e in audit.events if e.kind is AuditEventKind.SYSTEM]
    assert len(system_events) == 1
    assert "fact" in (system_events[0].detail or "").lower()


def test_extract_cross_tenant_blocked():
    extraction_svc, consultation_svc = _extraction_svc()
    c = _run(_consented_consultation(consultation_svc))
    with pytest.raises(ConsultationNotFound):
        _run(
            extraction_svc.extract(
                doctor_id=_DR_B,
                consultation_id=c.consultation_id,
                now=_NOW,
            )
        )
