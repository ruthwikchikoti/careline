"""Consultation lifecycle + DPDP consent stamping (NR-2).

Owns the Track A upstream use-case: create a consultation draft, stamp explicit
patient consent before any processing, and approve only when consent is active.
The service orchestrates domain models through :class:`ConsultationRepository` —
it adds no gate logic of its own beyond the fail-closed consent check on approve.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from careline.domain.model.consent import Consent
from careline.domain.model.consultation import Consultation
from careline.domain.model.fact import Fact
from careline.domain.ports.repositories import ConsultationRepository
from careline.services.audit_service import AuditEventKind, AuditService


class ConsultationNotFound(LookupError):
    """Raised when a consultation does not exist under the requesting doctor."""


class ConsentViolation(ValueError):
    """Raised when an operation requires active consent that is absent or withdrawn."""


class ConsultationService:
    """Create consultations, stamp consent, and approve with a DPDP fail-closed gate."""

    def __init__(
        self,
        *,
        repo: ConsultationRepository,
        audit: AuditService | None = None,
    ) -> None:
        self._repo = repo
        self._audit = audit

    async def create(
        self,
        *,
        doctor_id: str,
        patient_id: str,
        transcript: str | None = None,
        consultation_id: str | None = None,
        now: datetime | None = None,
    ) -> Consultation:
        """Open a new draft consultation (no consent until explicitly stamped)."""
        now = now or datetime.now(timezone.utc)
        consultation = Consultation(
            consultation_id=consultation_id or str(uuid.uuid4()),
            doctor_id=doctor_id,
            patient_id=patient_id,
            created_at=now,
            transcript=transcript,
        )
        await self._repo.save(consultation)
        return consultation

    async def stamp_consent(
        self,
        *,
        doctor_id: str,
        consultation_id: str,
        purpose: str,
        now: datetime | None = None,
    ) -> Consultation:
        """Mint an explicit consent grant and attach it to the consultation."""
        now = now or datetime.now(timezone.utc)
        consultation = await self._get_or_raise(
            doctor_id=doctor_id, consultation_id=consultation_id
        )
        grant = Consent.grant(
            subject_id=consultation.patient_id, purpose=purpose, at=now
        )
        updated = consultation.model_copy(update={"consent": grant})
        await self._repo.save(updated)
        self._log_consent(
            patient_id=consultation.patient_id,
            doctor_id=doctor_id,
            detail=f"consent granted for purpose: {purpose!r}",
            now=now,
        )
        return updated

    async def withdraw_consent(
        self,
        *,
        doctor_id: str,
        consultation_id: str,
        now: datetime | None = None,
    ) -> Consultation:
        """Withdraw active consent — irreversible for this grant (audit-retained)."""
        now = now or datetime.now(timezone.utc)
        consultation = await self._get_or_raise(
            doctor_id=doctor_id, consultation_id=consultation_id
        )
        if consultation.consent is None or not consultation.consent.is_active:
            raise ConsentViolation(
                "cannot withdraw consent: no active consent on this consultation"
            )
        try:
            withdrawn = consultation.consent.withdraw(at=now)
        except ValueError as exc:
            raise ConsentViolation(str(exc)) from exc
        updated = consultation.model_copy(update={"consent": withdrawn})
        await self._repo.save(updated)
        self._log_consent(
            patient_id=consultation.patient_id,
            doctor_id=doctor_id,
            detail="consent withdrawn",
            now=now,
        )
        return updated

    async def approve(
        self,
        *,
        doctor_id: str,
        consultation_id: str,
        now: datetime | None = None,
    ) -> Consultation:
        """Promote a consented draft to approved — fail-closed without active consent."""
        now = now or datetime.now(timezone.utc)
        consultation = await self._get_or_raise(
            doctor_id=doctor_id, consultation_id=consultation_id
        )
        if not consultation.is_processable:
            raise ConsentViolation(
                "cannot approve a consultation without active consent"
            )
        try:
            approved = consultation.approve()
        except ValueError as exc:
            raise ConsentViolation(str(exc)) from exc
        await self._repo.save(approved)
        return approved

    async def attach_facts(
        self,
        *,
        doctor_id: str,
        consultation_id: str,
        facts: tuple[Fact, ...],
        now: datetime | None = None,
    ) -> Consultation:
        """Attach drafted facts to a consented consultation (not yet approved)."""
        now = now or datetime.now(timezone.utc)
        consultation = await self._get_or_raise(
            doctor_id=doctor_id, consultation_id=consultation_id
        )
        if not consultation.is_processable:
            raise ConsentViolation(
                "cannot attach facts to a consultation without active consent"
            )
        updated = consultation.with_facts(facts)
        await self._repo.save(updated)
        return updated

    async def get(
        self, *, doctor_id: str, consultation_id: str
    ) -> Consultation | None:
        """Return the consultation if it exists under this doctor, else ``None``."""
        return await self._repo.get(
            doctor_id=doctor_id, consultation_id=consultation_id
        )

    async def list_for_patient(
        self, *, doctor_id: str, patient_id: str
    ) -> tuple[Consultation, ...]:
        """All consultations for one patient under one doctor."""
        return await self._repo.list_for_patient(
            doctor_id=doctor_id, patient_id=patient_id
        )

    async def list(
        self, *, doctor_id: str, limit: int = 50
    ) -> tuple[Consultation, ...]:
        """All consultations for a doctor, newest first."""
        return await self._repo.list_for_doctor(doctor_id=doctor_id, limit=limit)

    async def _get_or_raise(
        self, *, doctor_id: str, consultation_id: str
    ) -> Consultation:
        consultation = await self._repo.get(
            doctor_id=doctor_id, consultation_id=consultation_id
        )
        if consultation is None:
            raise ConsultationNotFound(
                f"consultation {consultation_id!r} not found for doctor {doctor_id!r}"
            )
        return consultation

    def _log_consent(
        self,
        *,
        patient_id: str,
        doctor_id: str,
        detail: str,
        now: datetime,
    ) -> None:
        if self._audit is None:
            return
        self._audit.log_event(
            AuditEventKind.CONSENT,
            patient_id=patient_id,
            doctor_id=doctor_id,
            detail=detail,
            logged_at=now,
        )


__all__ = [
    "ConsentViolation",
    "ConsultationNotFound",
    "ConsultationService",
]
