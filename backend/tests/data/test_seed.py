"""Seed patient tests (NG-2).

The canonical §8.3 seed must reproducibly demonstrate the core safety query: at
SEED_NOW the superseded medication and instruction are gone from the valid slice,
while the current ones (and the standing allergy/follow-up) remain.
"""

from careline.adapters.memory.seed import (
    SEED_DOCTOR_ID,
    SEED_NOW,
    SEED_PATIENT_ID,
    seed_patient,
)
from careline.domain.enums import FactKind


def test_seed_is_deterministic():
    assert seed_patient() == seed_patient()


def test_seed_belongs_to_one_tenant():
    p = seed_patient()
    assert p.doctor_id == SEED_DOCTOR_ID
    assert p.patient_id == SEED_PATIENT_ID


def test_valid_slice_drops_superseded_facts():
    vs = seed_patient().valid_slice(SEED_NOW)
    ids = set(vs.citations)
    assert "med-1" in ids and "instr-1" in ids  # current
    assert "med-2" not in ids  # discontinued antibiotic (T1)
    assert "instr-2" not in ids  # expired liquid-diet guidance (T2)


def test_valid_slice_keeps_standing_facts():
    vs = seed_patient().valid_slice(SEED_NOW)
    assert vs.of_kind(FactKind.ALLERGY)[0].id == "alg-1"
    assert vs.of_kind(FactKind.FOLLOW_UP)[0].id == "fu-1"


def test_superseded_facts_remain_in_history():
    hist_ids = {f.id for f in seed_patient().history(SEED_NOW)}
    assert hist_ids == {"med-2", "instr-2"}
