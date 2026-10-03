"""The Layer-1 persistence ports — the source-of-truth boundary (NG-3).

These ABCs are the contract between the application/services and the MongoDB
source of truth. Everything above them (Naresh's services, the brain) depends on
*these*, never on Motor or BSON — so the storage engine is swappable and the suite
can run against an in-memory mock.

Every method carries a **required keyword-only ``doctor_id``**. This is the same
structural-isolation rule as the memory port: there is no query path that is not
tenant-scoped, so a cross-patient / cross-tenant read is an absent code path, not a
forgotten check (a leak is sev-0). The concrete Mongo adapter folds ``doctor_id``
into a ``_scoped_filter`` that *leads* every query and index (NG-5).

Two safety-shaped methods deserve note:

* :meth:`PatientRepository.valid_slice` returns the *currently-valid, approved*
  facts — the same half-open + approval predicate the domain enforces, pushed down
  to the query so a superseded fact is never even read back as current.
* :meth:`PatientRepository.apply_facts` is the supersession write path (§B.6):
  applying a new fact atomically *closes* the fact it replaces. The repository owns
  this so "never two current versions of the same thing" is a storage invariant.

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime

from careline.domain.model.consultation import Consultation
from careline.domain.model.fact import Fact
from careline.domain.model.patient import Patient, PatientIdentity, ValidSlice


class PatientRepository(ABC):
    """Read/write access to the longitudinal record for one patient under one doctor."""

    async def list_for_doctor(self, *, doctor_id: str) -> list[tuple[str, int]]:
        """This doctor's registered patients as ``(patient_id, approved_fact_count)``.

        Concrete default returns empty so existing implementations and test doubles
        keep working unchanged; real repos override it.
        """
        return []

    async def find_by_patient_id(self, *, patient_id: str) -> PatientIdentity | None:
        """Resolve a registered patient's identity by ``patient_id`` (portal login).

        Used by the patient portal to look up the doctor scope + PIN hash for a
        patient signing in with their patient id. Concrete default returns ``None``
        so existing implementations/test doubles keep working; real repos override.
        """
        return None

    @abstractmethod
    async def get(self, *, doctor_id: str, patient_id: str) -> Patient | None:
        """The full aggregate (current + retired facts), or ``None`` if absent.

        ``None`` for a patient that belongs to *another* doctor — a cross-tenant
        read resolves to "not found", never to the other tenant's record.
        """
        raise NotImplementedError

    @abstractmethod
    async def exists(self, *, doctor_id: str, patient_id: str) -> bool:
        """True only if this patient exists *under this doctor*."""
        raise NotImplementedError

    @abstractmethod
    async def valid_slice(
        self, *, doctor_id: str, patient_id: str, now: datetime
    ) -> ValidSlice:
        """The approved, currently-valid facts at ``now`` — the grounding context.

        Pushes the half-open validity + approval predicate into the query, so a
        superseded or unapproved fact is never returned as current.
        """
        raise NotImplementedError

    @abstractmethod
    async def history(
        self, *, doctor_id: str, patient_id: str, now: datetime
    ) -> tuple[Fact, ...]:
        """The facts retired (superseded) as of ``now`` — for audit/explanation."""
        raise NotImplementedError

    @abstractmethod
    async def add_facts(
        self, *, doctor_id: str, patient_id: str, facts: tuple[Fact, ...]
    ) -> None:
        """Append facts verbatim (no supersession) — e.g. an initial seed/import."""
        raise NotImplementedError

    @abstractmethod
    async def apply_facts(
        self,
        *,
        doctor_id: str,
        patient_id: str,
        facts: tuple[Fact, ...],
        now: datetime,
    ) -> tuple[Fact, ...]:
        """Supersession write path (§B.6): apply new facts, retiring what they replace.

        For each incoming fact that conflicts with a currently-valid one (same kind
        + same identity), the existing fact's validity is *closed* at ``now`` and the
        new fact inserted with ``effective_from = now`` — half-open, no overlap, no
        gap. Returns the facts retired by this call (for audit). Atomic per patient.
        """
        raise NotImplementedError

    @abstractmethod
    async def soft_delete(self, *, doctor_id: str, patient_id: str) -> int:
        """DPDP erasure: null the clinical text but keep the skeleton; return count."""
        raise NotImplementedError

    @abstractmethod
    async def find_by_caller(
        self, *, doctor_id: str, caller_id: str
    ) -> PatientIdentity | None:
        """Lookup by caller-id under one doctor.

        ``None`` when the caller is unknown to this doctor — never another tenant's
        patient.
        """
        raise NotImplementedError

    @abstractmethod
    async def upsert_identity(self, *, identity: PatientIdentity) -> None:
        """Register or update caller-id/pin_hmac for a patient (doctor-scoped)."""
        raise NotImplementedError


class ConsultationRepository(ABC):
    """Persistence for consultation drafts/approvals (Track A upstream)."""

    @abstractmethod
    async def get(
        self, *, doctor_id: str, consultation_id: str
    ) -> Consultation | None:
        raise NotImplementedError

    @abstractmethod
    async def save(self, consultation: Consultation) -> None:
        """Insert or replace a consultation (scoped by its own ``doctor_id``)."""
        raise NotImplementedError

    @abstractmethod
    async def list_for_patient(
        self, *, doctor_id: str, patient_id: str
    ) -> tuple[Consultation, ...]:
        raise NotImplementedError

    @abstractmethod
    async def list_for_doctor(
        self, *, doctor_id: str, limit: int = 50
    ) -> tuple[Consultation, ...]:
        """All consultations under one doctor, newest first, capped at limit."""
        raise NotImplementedError


class AuditRepository(ABC):
    """Append-only access log (DPDP access-logging + the reasoning audit trail)."""

    @abstractmethod
    async def append(self, *, doctor_id: str, record: Mapping[str, object]) -> None:
        """Append one immutable audit record, tenant-scoped by ``doctor_id``."""
        raise NotImplementedError

    @abstractmethod
    async def soft_delete_for_patient(
        self, *, doctor_id: str, patient_id: str
    ) -> int:
        """Null the clinical text of this patient's audit rows, keep the skeleton."""
        raise NotImplementedError


class DoctorRepository(ABC):
    """Read access to the doctor (tenant) profile — thresholds, scope config, etc."""

    @abstractmethod
    async def get(self, *, doctor_id: str) -> Mapping[str, object] | None:
        """The doctor's stored profile document, or ``None`` if unknown."""
        raise NotImplementedError


__all__ = [
    "PatientRepository",
    "ConsultationRepository",
    "AuditRepository",
    "DoctorRepository",
]
