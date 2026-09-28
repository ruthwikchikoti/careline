"""DPDP right-to-erasure orchestration (NR-7).

Coordinates Layer-1 soft-delete, Layer-2 forget, and audit redaction under a
single ownership-checked entry point. Wrong-tenant or unknown patients raise
:class:`ErasedNothing` so the API can return a generic 404 (no-leak).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from careline.domain.ports.memory import MemoryProvider
from careline.domain.ports.repositories import PatientRepository
from careline.services.audit_service import AuditEventKind, AuditService

if TYPE_CHECKING:
    pass


class ErasedNothing(LookupError):
    """Raised when the patient is unknown to this doctor — maps to 404."""


@dataclass(frozen=True)
class ErasureResult:
    """Counts from each storage layer after a successful DPDP erase."""

    patient_id: str
    layer1_nulled: int
    layer2_dropped: bool
    audit_redacted: int


class DpdpService:
    """Orchestrate DPDP erasure across Layer-1, Layer-2, and audit."""

    def __init__(
        self,
        *,
        patient_repo: PatientRepository,
        memory: MemoryProvider,
        audit: AuditService,
    ) -> None:
        self._patient_repo = patient_repo
        self._memory = memory
        self._audit = audit

    async def erase(self, *, doctor_id: str, patient_id: str) -> ErasureResult:
        """Erase all clinical data for one patient under one doctor."""
        patient = await self._patient_repo.get(
            doctor_id=doctor_id,
            patient_id=patient_id,
        )
        if patient is None:
            raise ErasedNothing("patient not found")

        layer1_nulled = await self._patient_repo.soft_delete(
            doctor_id=doctor_id,
            patient_id=patient_id,
        )
        await self._memory.forget(doctor_id=doctor_id, patient_id=patient_id)
        audit_redacted = self._audit.redact_patient(patient_id)

        self._audit.log_event(
            AuditEventKind.ERASURE,
            patient_id=patient_id,
            doctor_id=doctor_id,
            detail=(
                f"DPDP erasure complete: layer1={layer1_nulled}, "
                f"layer2=True, audit={audit_redacted}"
            ),
        )

        return ErasureResult(
            patient_id=patient_id,
            layer1_nulled=layer1_nulled,
            layer2_dropped=True,
            audit_redacted=audit_redacted,
        )


__all__ = ["DpdpService", "ErasedNothing", "ErasureResult"]
