"""Domain ↔ BSON mapper tests (NG-4).

Every fact subtype must round-trip losslessly through the flat document shape, the
validity must hoist to top-level range columns, and a naive datetime (as a driver
may return) must normalise back to UTC — the date↔datetime bridge.
"""

from datetime import datetime, timezone

import pytest

from careline.adapters.mongo.mappers import doc_to_fact, fact_to_doc
from careline.domain.model.fact import (
    Allergy,
    Diagnosis,
    FollowUp,
    Instruction,
    Medication,
    Observation,
)
from careline.domain.model.temporal import Validity

PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)
SUP = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _med() -> Medication:
    return Medication(
        id="med-1",
        validity=Validity(effective_from=PAST, superseded_at=SUP),
        summary="Amoxicillin 250mg",
        name="Amoxicillin",
        dose="250mg",
        frequency="thrice daily",
        approved_by="dr-X",
        approved_at=PAST,
    )


def test_validity_is_hoisted_to_top_level_columns():
    doc = fact_to_doc(_med(), doctor_id="dr-X", patient_id="patient-A")
    assert doc["effective_from"] == PAST
    assert doc["superseded_at"] == SUP
    assert "validity" not in doc
    assert doc["_id"] == "med-1"
    assert doc["doctor_id"] == "dr-X" and doc["patient_id"] == "patient-A"


def test_open_validity_stores_none_superseded_at():
    f = _med().model_copy(update={"validity": Validity(effective_from=PAST)})
    doc = fact_to_doc(f, doctor_id="dr-X", patient_id="patient-A")
    assert doc["superseded_at"] is None


@pytest.mark.parametrize(
    "fact",
    [
        _med(),
        Instruction(id="i1", validity=Validity(effective_from=PAST), summary="rest", text="rest"),
        Diagnosis(id="d1", validity=Validity(effective_from=PAST), summary="flu", condition="influenza", code="J10"),
        Observation(id="o1", validity=Validity(effective_from=PAST), summary="bp", metric="blood pressure", value="120/80"),
        Allergy(id="a1", validity=Validity(effective_from=PAST), summary="penicillin", substance="penicillin", reaction="rash"),
        FollowUp(id="f1", validity=Validity(effective_from=PAST), summary="review", scheduled_for=SUP, with_whom="Dr X"),
    ],
)
def test_every_subtype_round_trips(fact):
    doc = fact_to_doc(fact, doctor_id="dr-X", patient_id="patient-A")
    assert doc_to_fact(doc) == fact  # exact equality, fields and kind preserved


def test_naive_datetime_normalises_to_utc():
    # Simulate a driver that hands back a naive (tz-less) datetime.
    doc = fact_to_doc(_med(), doctor_id="dr-X", patient_id="patient-A")
    doc["effective_from"] = doc["effective_from"].replace(tzinfo=None)
    doc["superseded_at"] = doc["superseded_at"].replace(tzinfo=None)
    rebuilt = doc_to_fact(doc)
    assert rebuilt.validity.effective_from == PAST  # back to aware UTC
    assert rebuilt.validity.superseded_at == SUP
