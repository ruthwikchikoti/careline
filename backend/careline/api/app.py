"""FastAPI application factory + lifespan DI (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from careline.adapters.orchestration.graph import build_default_graph
from careline.adapters.memory.local import LocalMemoryProvider
from careline.adapters.mongo.supersession import plan_supersession
from careline.api.errors import register_exception_handlers
from careline.api.routers import (
    audit_router,
    auth_router,
    brain_router,
    consultations_router,
    observability_router,
    patients_router,
)
from careline.config import Settings, get_settings
from careline.domain.model.consultation import Consultation
from careline.domain.model.fact import Fact
from careline.domain.model.patient import Patient, PatientIdentity, ValidSlice
from careline.domain.ports.repositories import ConsultationRepository, PatientRepository
from careline.services.approval_service import ApprovalService
from careline.services.audit_service import AuditService
from careline.services.auth_service import AuthService
from careline.services.consultation_service import ConsultationService
from careline.services.dpdp_service import DpdpService
from careline.services.extraction_service import ExtractionService, HeuristicExtractor
from careline.services.patient_lookup_service import PatientLookupService
from careline.services.question_service import QuestionService


class _InMemoryConsultationRepository(ConsultationRepository):
    """Offline consultation store — tenant-scoped like the Mongo adapter."""

    def __init__(self) -> None:
        self._store: dict[str, Consultation] = {}

    async def get(self, *, doctor_id: str, consultation_id: str) -> Consultation | None:
        consultation = self._store.get(consultation_id)
        if consultation is None or consultation.doctor_id != doctor_id:
            return None
        return consultation

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
    """Offline patient store — supersession via the same plan as Mongo."""

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
        for fact in patient.facts:
            if fact.id in close_ids:
                updated.append(fact.supersede(now))
            else:
                updated.append(fact)
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
        key = self._key(doctor_id=identity.doctor_id, patient_id=identity.patient_id)
        if key not in self._store:
            self._store[key] = Patient(
                patient_id=identity.patient_id,
                doctor_id=identity.doctor_id,
                facts=(),
            )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.is_production:
        settings.assert_prod_safe()

    audit = AuditService()
    mongo_client = None

    if settings.mongo_uri:
        from careline.adapters.mongo import (
            MongoConsultationRepository,
            MongoMemoryProvider,
            MongoPatientRepository,
            create_client,
            ensure_indexes,
        )

        mongo_client = create_client(settings.mongo_uri)
        database = mongo_client["careline"]
        await ensure_indexes(database)
        patient_repo = MongoPatientRepository(database)
        consultation_repo = MongoConsultationRepository(database)
        memory = MongoMemoryProvider(database)
    else:
        patient_repo = _InMemoryPatientRepository()
        consultation_repo = _InMemoryConsultationRepository()
        memory = LocalMemoryProvider()

    consultation_svc = ConsultationService(repo=consultation_repo, audit=audit)
    auth_svc = AuthService(settings=settings)
    patient_lookup_svc = PatientLookupService(patient_repo=patient_repo, settings=settings)
    extraction_svc = ExtractionService(
        extractor=HeuristicExtractor(),
        consultation_svc=consultation_svc,
        audit=audit,
    )
    approval_svc = ApprovalService(
        consultation_svc=consultation_svc,
        patient_repo=patient_repo,
        memory=memory,
        audit=audit,
    )
    graph = build_default_graph(thresholds=settings.to_thresholds())
    question_svc = QuestionService(graph=graph, audit=audit)
    dpdp_svc = DpdpService(patient_repo=patient_repo, memory=memory, audit=audit)

    app.state.settings = settings
    app.state.audit = audit
    app.state.auth_svc = auth_svc
    app.state.patient_lookup_svc = patient_lookup_svc
    app.state.patient_repo = patient_repo
    app.state.consultation_svc = consultation_svc
    app.state.extraction_svc = extraction_svc
    app.state.approval_svc = approval_svc
    app.state.graph = graph
    app.state.question_svc = question_svc
    app.state.dpdp_svc = dpdp_svc
    app.state.audit = audit
    app.state.mongo_client = mongo_client

    yield

    if mongo_client is not None:
        mongo_client.close()


def create_app(*, settings: Settings | None = None) -> FastAPI:
    """Build the CareLine FastAPI application."""
    app = FastAPI(title="CareLine", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(patients_router)
    app.include_router(consultations_router)
    app.include_router(audit_router)
    app.include_router(brain_router)
    app.include_router(observability_router)
    if settings is not None:
        app.state.settings = settings
    return app


__all__ = ["create_app"]
