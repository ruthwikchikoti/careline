"""sev-0 cross-tenant isolation suite (NG-7).

The single most important property in CareLine: **one patient per call, zero
cross-patient reachability.** A leak of another patient's — or another doctor's —
data is a sev-0 incident. Isolation here is *structural*, not a runtime check: every
repository/memory query leads with ``doctor_id``, so a wrong-tenant access has no
code path to the data and resolves to "nothing" (→ 404), never to someone else's row.

These tests assert that property end-to-end against the Layer-1 repositories and the
Layer-2 memory provider, including the adversarial case of **two doctors sharing the
same patient_id string** — the namespace must keep them completely separate.

The same assertions run against a real MongoDB via testcontainers when one is
available; absent Docker/testcontainers, that variant is skipped (the in-memory
variant always runs, so the safety property is always checked).
"""

import asyncio
from datetime import datetime, timezone

from careline.adapters.mongo.memory import MongoMemoryProvider
from careline.adapters.mongo.repositories import MongoPatientRepository
from careline.domain.model.fact import Medication
from careline.domain.model.patient import Patient
from careline.domain.model.temporal import Validity

PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 6, 15, tzinfo=timezone.utc)

DR_A, DR_B = "dr-A", "dr-B"
SHARED_PID = "patient-1"  # same id string under both doctors — the adversarial case


def _med(id_, name, doctor_id) -> Medication:
    return Medication(
        id=id_,
        validity=Validity(effective_from=PAST),
        summary=f"{name} for {doctor_id}",
        name=name,
        approved_by=doctor_id,
        approved_at=PAST,
    )


def _two_tenant_repo(mongo_db) -> MongoPatientRepository:
    repo = MongoPatientRepository(mongo_db)
    # Both doctors have a patient with the SAME patient_id but different data.
    asyncio.run(repo.add_facts(doctor_id=DR_A, patient_id=SHARED_PID, facts=(_med("a-1", "Aspirin", DR_A),)))
    asyncio.run(repo.add_facts(doctor_id=DR_B, patient_id=SHARED_PID, facts=(_med("b-1", "Beta-blocker", DR_B),)))
    return repo


def test_get_is_scoped_to_the_owning_doctor(mongo_db):
    repo = _two_tenant_repo(mongo_db)
    a = asyncio.run(repo.get(doctor_id=DR_A, patient_id=SHARED_PID))
    b = asyncio.run(repo.get(doctor_id=DR_B, patient_id=SHARED_PID))
    assert {f.id for f in a.facts} == {"a-1"}  # only DR_A's fact
    assert {f.id for f in b.facts} == {"b-1"}  # only DR_B's fact
    # No bleed: DR_A never sees DR_B's fact and vice versa.
    assert "b-1" not in {f.id for f in a.facts}
    assert "a-1" not in {f.id for f in b.facts}


def test_unknown_doctor_gets_nothing_not_someone_elses_record(mongo_db):
    repo = _two_tenant_repo(mongo_db)
    # A doctor who owns no such patient must get None — never a fallback to another's.
    assert asyncio.run(repo.get(doctor_id="dr-INTRUDER", patient_id=SHARED_PID)) is None
    assert asyncio.run(repo.exists(doctor_id="dr-INTRUDER", patient_id=SHARED_PID)) is False


def test_valid_slice_never_crosses_tenants(mongo_db):
    repo = _two_tenant_repo(mongo_db)
    vs = asyncio.run(repo.valid_slice(doctor_id=DR_A, patient_id=SHARED_PID, now=NOW))
    assert set(vs.citations) == {"a-1"}
    assert "b-1" not in vs.citations


def test_soft_delete_only_erases_the_owning_tenant(mongo_db):
    repo = _two_tenant_repo(mongo_db)
    asyncio.run(repo.soft_delete(doctor_id=DR_A, patient_id=SHARED_PID))
    # DR_B's identically-keyed patient is untouched by DR_A's erasure.
    b = asyncio.run(repo.valid_slice(doctor_id=DR_B, patient_id=SHARED_PID, now=NOW))
    assert set(b.citations) == {"b-1"}


def test_memory_retrieval_never_crosses_tenants(mongo_db):
    provider = MongoMemoryProvider(mongo_db)
    pa = Patient(patient_id=SHARED_PID, doctor_id=DR_A, facts=(_med("a-1", "Aspirin", DR_A),))
    pb = Patient(patient_id=SHARED_PID, doctor_id=DR_B, facts=(_med("b-1", "Betablocker", DR_B),))
    asyncio.run(provider.index(doctor_id=DR_A, patient_id=SHARED_PID, slice=pa.valid_slice(NOW)))
    asyncio.run(provider.index(doctor_id=DR_B, patient_id=SHARED_PID, slice=pb.valid_slice(NOW)))

    # DR_A querying for DR_B's drug must surface only DR_A's own namespace.
    hits = asyncio.run(
        provider.retrieve(doctor_id=DR_A, patient_id=SHARED_PID, query="betablocker aspirin")
    )
    assert all(h.fact_id == "a-1" for h in hits)
    assert all(h.fact_id != "b-1" for h in hits)


# --- real-MongoDB variant (skipped without Docker/testcontainers) -------------


def test_isolation_holds_on_real_mongo():
    """Same scope assertions against a real MongoDB via testcontainers.

    Skipped unless ``testcontainers`` (and Docker) are available; the in-memory
    variant above always runs, so the sev-0 property is never left unchecked.
    """
    import pytest

    pytest.importorskip("testcontainers.mongodb")
    try:
        from testcontainers.mongodb import MongoDbContainer  # type: ignore
    except Exception:  # pragma: no cover - environment without the extra
        pytest.skip("testcontainers not available")

    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError:  # pragma: no cover
        pytest.skip("motor not available")

    try:
        with MongoDbContainer("mongo:7") as mongo:  # pragma: no cover - needs Docker
            client = AsyncIOMotorClient(mongo.get_connection_url(), tz_aware=True)
            db = client["careline_sev0"]
            repo = MongoPatientRepository(db)
            asyncio.run(repo.add_facts(doctor_id=DR_A, patient_id=SHARED_PID, facts=(_med("a-1", "Aspirin", DR_A),)))
            asyncio.run(repo.add_facts(doctor_id=DR_B, patient_id=SHARED_PID, facts=(_med("b-1", "Beta", DR_B),)))
            a = asyncio.run(repo.get(doctor_id=DR_A, patient_id=SHARED_PID))
            assert {f.id for f in a.facts} == {"a-1"}
            assert asyncio.run(repo.get(doctor_id="dr-INTRUDER", patient_id=SHARED_PID)) is None
    except Exception as exc:  # pragma: no cover - Docker unavailable in CI
        pytest.skip(f"real MongoDB unavailable: {exc}")
