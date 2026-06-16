"""Fact + subtype tests (NG-1).

These pin the two safety stamps every fact carries (temporal validity + doctor
approval) and the ``is_current`` conjunction that fuses them, plus the kind-pinning
that stops a fact being mislabelled.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from careline.domain.enums import FactKind
from careline.domain.model.fact import (
    Allergy,
    Diagnosis,
    Fact,
    FollowUp,
    Instruction,
    Medication,
    Observation,
)
from careline.domain.model.temporal import Validity

JAN = datetime(2026, 1, 1)
FEB = datetime(2026, 2, 1)
MAR = datetime(2026, 3, 1)


def _med(**kw) -> Medication:
    base = dict(
        id="m1",
        validity=Validity(effective_from=JAN),
        summary="Amoxicillin 500mg, twice daily",
        name="Amoxicillin",
    )
    base.update(kw)
    return Medication(**base)


# -- approval stamp ----------------------------------------------------------


def test_unapproved_fact_is_not_approved():
    assert _med().is_approved is False


def test_approve_stamps_doctor_and_instant():
    approved = _med().approve(by="dr_house", at=JAN)
    assert approved.is_approved is True
    assert approved.approved_by == "dr_house"
    assert approved.approved_at == JAN


def test_approved_at_without_approver_is_rejected():
    with pytest.raises(ValidationError):
        _med(approved_at=JAN)


# -- the is_current conjunction ----------------------------------------------


def test_is_current_requires_both_approval_and_validity():
    approved_valid = _med().approve(by="dr", at=JAN)
    assert approved_valid.is_current(FEB) is True


def test_unapproved_but_valid_is_not_current():
    assert _med().is_current(FEB) is False


def test_approved_but_superseded_is_not_current():
    retired = _med().approve(by="dr", at=JAN).supersede(FEB)
    assert retired.is_valid_at(JAN) is True
    assert retired.is_current(MAR) is False  # superseded before now


def test_approved_but_not_yet_effective_is_not_current():
    future = _med(validity=Validity(effective_from=MAR)).approve(by="dr", at=JAN)
    assert future.is_current(FEB) is False


# -- kind pinning ------------------------------------------------------------


def test_each_subtype_pins_its_kind():
    assert Medication(id="m", validity=Validity(effective_from=JAN), summary="s", name="x").kind is FactKind.MEDICATION
    assert Instruction(id="i", validity=Validity(effective_from=JAN), summary="s", text="rest").kind is FactKind.INSTRUCTION
    assert Diagnosis(id="d", validity=Validity(effective_from=JAN), summary="s", condition="flu").kind is FactKind.DIAGNOSIS
    assert Observation(id="o", validity=Validity(effective_from=JAN), summary="s", metric="bp", value="120/80").kind is FactKind.OBSERVATION
    assert Allergy(id="a", validity=Validity(effective_from=JAN), summary="s", substance="penicillin").kind is FactKind.ALLERGY
    assert FollowUp(id="f", validity=Validity(effective_from=JAN), summary="s").kind is FactKind.FOLLOW_UP


def test_subtype_kind_cannot_be_overridden():
    with pytest.raises(ValidationError):
        Medication(
            id="m",
            kind=FactKind.ALLERGY,  # lying about the kind
            validity=Validity(effective_from=JAN),
            summary="s",
            name="x",
        )


def test_fact_is_frozen_and_forbids_extra_fields():
    f = _med()
    with pytest.raises(ValidationError):
        f.summary = "mutated"
    with pytest.raises(ValidationError):
        _med(surprise="nope")


def test_supersede_retires_the_fact_non_destructively():
    f = _med()
    retired = f.supersede(FEB)
    assert retired.validity.superseded_at == FEB
    assert f.validity.is_open is True  # original untouched
