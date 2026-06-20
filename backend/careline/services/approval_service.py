"""One-tap HITL approval — draft facts to live Layer-1 + Layer-2 (NR-4).

Owns the doctor sign-off use-case: stamp approval on drafted facts, apply them
through Naga's §B.6 supersession write path, rebuild Layer-2 memory from the fresh
valid slice, promote the consultation to ``approved``, and audit. Consultation
status flips only after both Layer-1 and Layer-2 writes succeed — fail-closed.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from careline.domain.model.consultation import Consultation
from careline.domain.ports.memory import MemoryProvider
from careline.domain.ports.repositories import PatientRepository
from careline.services.audit_service import AuditEventKind, AuditService
from careline.services.consultation_service import (
    ConsentViolation,
    ConsultationNotFound,
    ConsultationService,
)


class NoFactsError(ValueError):
    """Raised when approval is requested on a consultation with no drafted facts."""


class AlreadyApprovedError(ValueError):
    """Raised when approval is requested on a non-draft consultation."""


@dataclass(frozen=True)
class ApprovalResult:
    """Outcome of a one-tap approval — consultation plus write counts."""

    consultation: Consultation
    applied_facts: int
    retired_facts: int


class ApprovalService:
    """Doctor one-tap approve: stamp facts, apply supersession, index memory."""

    def __init__(
        self,
        *,
        consultation_svc: ConsultationService,
        patient_repo: PatientRepository,
        memory: MemoryProvider,
        audit: AuditService | None = None,
    ) -> None:
        self._consultation_svc = consultation_svc
        self._patient_repo = patient_repo
        self._memory = memory
        self._audit = audit

    async def approve(
        self,
        *,
        doctor_id: str,
        consultation_id: str,
        now: datetime | None = None,
    ) -> ApprovalResult:
        """Approve drafted facts and promote them into the patient's live context."""
        now = now or datetime.now(timezone.utc)
        consultation = await self._consultation_svc.get(
            doctor_id=doctor_id, consultation_id=consultation_id
        )
        if consultation is None:
            raise ConsultationNotFound(
                f"consultation {consultation_id!r} not found for doctor {doctor_id!r}"
            )
        if not consultation.is_processable:
            raise ConsentViolation(
                "cannot approve a consultation without active consent"
            )
        if consultation.status != "draft":
            raise AlreadyApprovedError(
                f"only a draft consultation can be approved (status={consultation.status})"
            )
        if not consultation.facts:
            raise NoFactsError(
                "cannot approve a consultation with no drafted facts"
            )

        approved_facts = tuple(f.approve(by=doctor_id, at=now) for f in consultation.facts)
        retired = await self._patient_repo.apply_facts(
            doctor_id=doctor_id,
            patient_id=consultation.patient_id,
            facts=approved_facts,
            now=now,
        )
        slice = await self._patient_repo.valid_slice(
            doctor_id=doctor_id,
            patient_id=consultation.patient_id,
            now=now,
        )
        await self._memory.index(
            doctor_id=doctor_id,
            patient_id=consultation.patient_id,
            slice=slice,
        )
        await self._consultation_svc.attach_facts(
            doctor_id=doctor_id,
            consultation_id=consultation_id,
            facts=approved_facts,
            now=now,
        )
        approved_consultation = await self._consultation_svc.approve(
            doctor_id=doctor_id,
            consultation_id=consultation_id,
            now=now,
        )
        if self._audit is not None:
            self._audit.log_event(
                AuditEventKind.SYSTEM,
                patient_id=consultation.patient_id,
                doctor_id=doctor_id,
                detail=f"{len(approved_facts)} fact(s) approved and applied",
                logged_at=now,
            )
        return ApprovalResult(
            consultation=approved_consultation,
            applied_facts=len(approved_facts),
            retired_facts=len(retired),
        )


__all__ = [
    "AlreadyApprovedError",
    "ApprovalResult",
    "ApprovalService",
    "NoFactsError",
]
