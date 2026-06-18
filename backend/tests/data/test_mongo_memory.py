"""MongoMemoryProvider tests (NG-6), against the in-memory async Mongo double.

The persisted Layer-2 twin must behave identically to the offline provider: indexes
only valid facts, retrieves within the patient namespace, and forgets on erasure.
"""

import asyncio

from careline.adapters.memory.seed import SEED_DOCTOR_ID, SEED_NOW, SEED_PATIENT_ID, seed_patient
from careline.adapters.mongo.memory import MongoMemoryProvider


def _indexed(mongo_db) -> MongoMemoryProvider:
    provider = MongoMemoryProvider(mongo_db)
    vs = seed_patient().valid_slice(SEED_NOW)
    asyncio.run(
        provider.index(doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID, slice=vs)
    )
    return provider


def _retrieve(provider, *, doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID, query):
    return asyncio.run(
        provider.retrieve(doctor_id=doctor_id, patient_id=patient_id, query=query)
    )


def test_retrieves_relevant_fact(mongo_db):
    hits = _retrieve(_indexed(mongo_db), query="what diet should I follow, spicy food?")
    assert hits[0].fact_id == "instr-1"


def test_reindex_replaces_namespace(mongo_db):
    provider = _indexed(mongo_db)
    # Re-index with an empty slice → namespace cleared, no stale docs left behind.
    from careline.domain.model.patient import ValidSlice

    asyncio.run(
        provider.index(
            doctor_id=SEED_DOCTOR_ID,
            patient_id=SEED_PATIENT_ID,
            slice=ValidSlice(as_of=SEED_NOW, facts=()),
        )
    )
    assert _retrieve(provider, query="diet") == ()


def test_cross_patient_retrieval_returns_nothing(mongo_db):
    hits = _retrieve(_indexed(mongo_db), patient_id="patient-Z", query="diet")
    assert hits == ()


def test_forget_clears_namespace(mongo_db):
    provider = _indexed(mongo_db)
    asyncio.run(provider.forget(doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID))
    assert _retrieve(provider, query="diet") == ()
