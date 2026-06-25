"""Mongo implementations of the Layer-1 persistence ports (NG-6).

The concrete source-of-truth repositories. Each is constructed with a Motor-style
``database`` handle (duck-typed, so a mock database drops straight in) and composes
the three building blocks from the rest of the package: :mod:`mappers` for the
document shape, :mod:`filters` for the tenant-scoped + temporal queries, and
:mod:`supersession` for the §B.6 write plan. They add no new safety logic — they
*execute* what those pure modules decide.

Every read and write flows through :func:`~careline.adapters.mongo.filters.scoped_filter`,
so the ``doctor_id`` scope is structural: a wrong-tenant read returns nothing (→ the
service layer turns that into a 404), never another tenant's data.

Owner: Naga (scope ``data``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from careline.adapters.mongo.client import AUDIT, CONSULTATIONS, DOCTORS, FACTS, PATIENTS
from careline.adapters.mongo.filters import (
    caller_filter,
    history_filter,
    scoped_filter,
    valid_slice_filter,
)
from careline.adapters.mongo.mappers import doc_to_fact, fact_to_doc
from careline.adapters.mongo.supersession import plan_supersession
from careline.domain.model.consultation import Consultation
from careline.domain.model.fact import Fact
from careline.domain.model.patient import Patient, PatientIdentity, ValidSlice
from careline.domain.ports.repositories import (
    AuditRepository,
    ConsultationRepository,
    DoctorRepository,
    PatientRepository,
)

# Free-text / clinical fields nulled on DPDP erasure (the skeleton — ids, kind,
# validity — is retained for audit continuity).
_REDACTABLE = (
    "summary", "name", "dose", "frequency", "route", "text", "condition", "code",
    "metric", "value", "unit", "substance", "reaction", "severity", "with_whom",
)


class MongoPatientRepository(PatientRepository):
    """The longitudinal record, backed by the ``facts`` collection."""

    def __init__(self, database: Any) -> None:
        self._facts = database[FACTS]
        self._patients = database[PATIENTS]

    async def get(self, *, doctor_id: str, patient_id: str) -> Patient | None:
        docs = await self._facts.find(
            scoped_filter(doctor_id=doctor_id, patient_id=patient_id)
        ).to_list(length=None)
        if docs:
            facts = tuple(doc_to_fact(d) for d in docs)
            return Patient(patient_id=patient_id, doctor_id=doctor_id, facts=facts)
        registered = await self._patients.find_one(
            scoped_filter(doctor_id=doctor_id, patient_id=patient_id)
        )
        if registered is None:
            return None
        return Patient(patient_id=patient_id, doctor_id=doctor_id, facts=())

    async def list_for_doctor(self, *, doctor_id: str) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        async for d in self._patients.find(
            scoped_filter(doctor_id=doctor_id), {"patient_id": 1}
        ):
            counts.setdefault(d["patient_id"], 0)
        async for d in self._facts.find(
            {**scoped_filter(doctor_id=doctor_id), "approved_by": {"$ne": None}},
            {"patient_id": 1},
        ):
            counts[d["patient_id"]] = counts.get(d["patient_id"], 0) + 1
        return sorted(counts.items())

    async def find_by_patient_id(self, *, patient_id: str) -> PatientIdentity | None:
        doc = await self._patients.find_one({"patient_id": patient_id})
        return _doc_to_identity(doc) if doc else None

    async def exists(self, *, doctor_id: str, patient_id: str) -> bool:
        n = await self._facts.count_documents(
            scoped_filter(doctor_id=doctor_id, patient_id=patient_id)
        )
        return n > 0

    async def valid_slice(
        self, *, doctor_id: str, patient_id: str, now: datetime
    ) -> ValidSlice:
        docs = await self._facts.find(
            valid_slice_filter(doctor_id=doctor_id, patient_id=patient_id, now=now)
        ).to_list(length=None)
        facts = tuple(doc_to_fact(d) for d in docs)
        return ValidSlice(as_of=now, facts=facts)

    async def history(
        self, *, doctor_id: str, patient_id: str, now: datetime
    ) -> tuple[Fact, ...]:
        docs = await self._facts.find(
            history_filter(doctor_id=doctor_id, patient_id=patient_id, now=now)
        ).to_list(length=None)
        facts = [doc_to_fact(d) for d in docs]
        facts.sort(key=lambda f: f.validity.superseded_at, reverse=True)  # type: ignore[arg-type,return-value]
        return tuple(facts)

    async def add_facts(
        self, *, doctor_id: str, patient_id: str, facts: tuple[Fact, ...]
    ) -> None:
        if not facts:
            return
        docs = [fact_to_doc(f, doctor_id=doctor_id, patient_id=patient_id) for f in facts]
        await self._facts.insert_many(docs)

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
        current = await self.valid_slice(doctor_id=doctor_id, patient_id=patient_id, now=now)
        plan = plan_supersession(current=current.facts, incoming=facts, now=now)

        retired: tuple[Fact, ...] = ()
        if plan.to_close:
            retired = tuple(f for f in current.facts if f.id in set(plan.to_close))
            await self._facts.update_many(
                scoped_filter(
                    doctor_id=doctor_id,
                    patient_id=patient_id,
                    _id={"$in": list(plan.to_close)},
                ),
                {"$set": {"superseded_at": now}},
            )
        docs = [
            fact_to_doc(f, doctor_id=doctor_id, patient_id=patient_id)
            for f in plan.to_insert
        ]
        if docs:
            await self._facts.insert_many(docs)
        return retired

    async def soft_delete(self, *, doctor_id: str, patient_id: str) -> int:
        """DPDP erasure: null clinical text and de-approve, keeping the skeleton.

        De-approving (``approved_by = None``) drops every fact out of the valid-slice
        query, so the patient is no longer answerable, while the redacted rows remain
        for audit continuity.
        """
        nulls: dict[str, Any] = {k: None for k in _REDACTABLE}
        nulls["summary"] = "[erased]"  # summary is non-optional in the domain model
        nulls["approved_by"] = None
        nulls["approved_at"] = None
        result = await self._facts.update_many(
            scoped_filter(doctor_id=doctor_id, patient_id=patient_id),
            {"$set": nulls},
        )
        return int(getattr(result, "modified_count", 0))

    async def find_by_caller(
        self, *, doctor_id: str, caller_id: str
    ) -> PatientIdentity | None:
        doc = await self._patients.find_one(
            caller_filter(doctor_id=doctor_id, caller_id=caller_id)
        )
        return _doc_to_identity(doc) if doc else None

    async def upsert_identity(self, *, identity: PatientIdentity) -> None:
        doc = _identity_to_doc(identity)
        await self._patients.update_one(
            {"doctor_id": identity.doctor_id, "caller_id": identity.caller_id},
            {"$set": doc},
            upsert=True,
        )


class MongoConsultationRepository(ConsultationRepository):
    """Consultation drafts/approvals, backed by the ``consultations`` collection."""

    def __init__(self, database: Any) -> None:
        self._col = database[CONSULTATIONS]

    async def get(self, *, doctor_id: str, consultation_id: str) -> Consultation | None:
        doc = await self._col.find_one(
            scoped_filter(doctor_id=doctor_id, _id=consultation_id)
        )
        return _doc_to_consultation(doc) if doc else None

    async def save(self, consultation: Consultation) -> None:
        doc = _consultation_to_doc(consultation)
        await self._col.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def list_for_patient(
        self, *, doctor_id: str, patient_id: str
    ) -> tuple[Consultation, ...]:
        docs = await self._col.find(
            scoped_filter(doctor_id=doctor_id, patient_id=patient_id)
        ).to_list(length=None)
        return tuple(_doc_to_consultation(d) for d in docs)

    async def list_for_doctor(
        self, *, doctor_id: str, limit: int = 50
    ) -> tuple[Consultation, ...]:
        docs = await self._col.find(
            scoped_filter(doctor_id=doctor_id)
        ).sort("created_at", -1).limit(limit).to_list(length=limit)
        return tuple(_doc_to_consultation(d) for d in docs)


class MongoAuditRepository(AuditRepository):
    """Append-only access log, backed by the ``audit`` collection."""

    def __init__(self, database: Any) -> None:
        self._col = database[AUDIT]

    async def append(self, *, doctor_id: str, record: Mapping[str, object]) -> None:
        doc = {**record, "doctor_id": doctor_id}
        await self._col.insert_one(doc)

    async def soft_delete_for_patient(
        self, *, doctor_id: str, patient_id: str
    ) -> int:
        result = await self._col.update_many(
            scoped_filter(doctor_id=doctor_id, patient_id=patient_id),
            {"$set": {"clinical_text": None, "redacted": True}},
        )
        return int(getattr(result, "modified_count", 0))


class MongoDoctorRepository(DoctorRepository):
    """Read access to the doctor (tenant) profile, backed by ``doctors``."""

    def __init__(self, database: Any) -> None:
        self._col = database[DOCTORS]

    async def get(self, *, doctor_id: str) -> Mapping[str, object] | None:
        return await self._col.find_one({"_id": doctor_id})


# --- consultation (de)serialisation -----------------------------------------


def _identity_to_doc(identity: PatientIdentity) -> dict[str, Any]:
    return {
        "_id": f"{identity.doctor_id}:{identity.patient_id}",
        "doctor_id": identity.doctor_id,
        "patient_id": identity.patient_id,
        "caller_id": identity.caller_id,
        "pin_hmac": identity.pin_hmac,
    }


def _doc_to_identity(doc: Mapping[str, Any]) -> PatientIdentity:
    return PatientIdentity(
        patient_id=doc["patient_id"],
        doctor_id=doc["doctor_id"],
        caller_id=doc["caller_id"],
        pin_hmac=doc["pin_hmac"],
    )


def _consultation_to_doc(c: Consultation) -> dict[str, Any]:
    return {
        "_id": c.consultation_id,
        "doctor_id": c.doctor_id,
        "patient_id": c.patient_id,
        "created_at": c.created_at,
        "status": c.status,
        "transcript": c.transcript,
        "consent": c.consent.model_dump() if c.consent else None,
        "facts": [
            fact_to_doc(f, doctor_id=c.doctor_id, patient_id=c.patient_id)
            for f in c.facts
        ],
    }


def _doc_to_consultation(doc: Mapping[str, Any]) -> Consultation:
    from careline.domain.model.consent import Consent

    consent = Consent(**doc["consent"]) if doc.get("consent") else None
    facts = tuple(doc_to_fact(d) for d in doc.get("facts", []))
    return Consultation(
        consultation_id=doc["_id"],
        doctor_id=doc["doctor_id"],
        patient_id=doc["patient_id"],
        created_at=doc["created_at"],
        status=doc.get("status", "draft"),
        transcript=doc.get("transcript"),
        consent=consent,
        facts=facts,
    )


__all__ = [
    "MongoPatientRepository",
    "MongoConsultationRepository",
    "MongoAuditRepository",
    "MongoDoctorRepository",
]
