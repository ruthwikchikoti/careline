"""Layer-1 MongoDB source-of-truth (owner: Naga)."""

from careline.adapters.mongo.client import create_client, ensure_indexes
from careline.adapters.mongo.memory import MongoMemoryProvider
from careline.adapters.mongo.repositories import (
    MongoAuditRepository,
    MongoConsultationRepository,
    MongoDoctorRepository,
    MongoPatientRepository,
)

__all__ = [
    "create_client",
    "ensure_indexes",
    "MongoPatientRepository",
    "MongoConsultationRepository",
    "MongoAuditRepository",
    "MongoDoctorRepository",
    "MongoMemoryProvider",
]
