"""Mongo repository tests (NG-6), against the in-memory async Mongo double.

These exercise the real repository code paths — scoped queries, the valid-slice
range query, the §B.6 supersession write, and DPDP soft-delete — proving the
Layer-1 source-of-truth behaves like the in-memory domain aggregate.
"""

import asyncio
from datetime import datetime, timezone

from careline.adapters.memory.seed import SEED_DOCTOR_ID, SEED_NOW, SEED_PATIENT_ID, seed_patient
from careline.adapters.mongo.repositories import MongoPatientRepository
from careline.domain.model.fact import Medication
from careline.domain.model.temporal import Validity

PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _seeded_repo(mongo_db) -> MongoPatientRepository:
    repo = MongoPatientRepository(mongo_db)
    p = seed_patient()
    asyncio.run(
        repo.add_facts(doctor_id=p.doctor_id, patient_id=p.patient_id, facts=p.facts)
    )
    return repo


def test_get_rebuilds_the_aggregate(mongo_db):
    repo = _seeded_repo(mongo_db)
    p = asyncio.run(repo.get(doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID))
    assert p is not None
    assert {f.id for f in p.facts} == {f.id for f in seed_patient().facts}


def test_valid_slice_matches_the_domain_query(mongo_db):
    repo = _seeded_repo(mongo_db)
    vs = asyncio.run(
        repo.valid_slice(doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID, now=SEED_NOW)
    )
    expected = set(seed_patient().valid_slice(SEED_NOW).citations)
    assert set(vs.citations) == expected
    assert "med-2" not in vs.citations  # superseded antibiotic dropped by the query


def test_history_returns_retired_facts(mongo_db):
    repo = _seeded_repo(mongo_db)
    hist = asyncio.run(
        repo.history(doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID, now=SEED_NOW)
    )
    assert {f.id for f in hist} == {"med-2", "instr-2"}


def test_get_unknown_patient_returns_none(mongo_db):
    repo = _seeded_repo(mongo_db)
    assert asyncio.run(repo.get(doctor_id=SEED_DOCTOR_ID, patient_id="nobody")) is None


def test_apply_facts_supersedes_same_drug(mongo_db):
    repo = _seeded_repo(mongo_db)
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    new_dose = Medication(
        id="med-1b",
        validity=Validity(effective_from=PAST),
        summary="Paracetamol 1000mg twice daily.",
        name="Paracetamol",
        dose="1000mg",
        approved_by=SEED_DOCTOR_ID,
        approved_at=PAST,
    )
    retired = asyncio.run(
        repo.apply_facts(
            doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID, facts=(new_dose,), now=now
        )
    )
    assert [f.id for f in retired] == ["med-1"]  # old Paracetamol closed

    vs = asyncio.run(
        repo.valid_slice(doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID, now=now)
    )
    para = [f for f in vs.facts if getattr(f, "name", None) == "Paracetamol"]
    assert [f.id for f in para] == ["med-1b"]  # exactly one current Paracetamol


def test_soft_delete_removes_patient_from_valid_slice(mongo_db):
    repo = _seeded_repo(mongo_db)
    count = asyncio.run(repo.soft_delete(doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID))
    assert count > 0
    vs = asyncio.run(
        repo.valid_slice(doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID, now=SEED_NOW)
    )
    assert vs.is_empty  # de-approved → unanswerable, but skeleton rows remain
