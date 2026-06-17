"""Abstract ports (reasoning, memory, repositories)."""

from careline.domain.ports.memory import MemoryHit, MemoryProvider
from careline.domain.ports.repositories import (
    AuditRepository,
    ConsultationRepository,
    DoctorRepository,
    PatientRepository,
)

__all__ = [
    "MemoryHit",
    "MemoryProvider",
    "PatientRepository",
    "ConsultationRepository",
    "AuditRepository",
    "DoctorRepository",
]
