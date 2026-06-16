"""Patient aggregate + ValidSlice tests (NG-1).

These pin the core safety query: the valid slice surfaces *only* approved facts
valid right now — a superseded fact and an unapproved fact both fall out — and the
retired versions remain reachable through ``history`` for audit. This is the
"valid-slice drops a superseded fact" demo, asserted.
"""

from datetime import datetime

from careline.domain.enums import FactKind
from careline.domain.model.fact import Instruction, Medication
from careline.domain.model.patient import Patient, ValidSlice
from careline.domain.model.temporal import Validity

JAN = datetime(2026, 1, 1)
FEB = datetime(2026, 2, 1)
MAR = datetime(2026, 3, 1)
APR = datetime(2026, 4, 1)


def _med(id_, name, *, eff=JAN, sup=None, approved=True) -> Medication:
    m = Medication(
        id=id_,
        validity=Validity(effective_from=eff, superseded_at=sup),
        summary=f"{name}",
        name=name,
    )
    return m.approve(by="dr", at=eff) if approved else m


def test_valid_slice_drops_a_superseded_fact():
    # old dose superseded by a new dose in FEB
    old = _med("m_old", "Amoxicillin 250mg", eff=JAN, sup=FEB)
    new = _med("m_new", "Amoxicillin 500mg", eff=FEB)
    p = Patient(patient_id="p1", doctor_id="dr", facts=(old, new))

    # in January, the old dose is the valid one
    jan_slice = p.valid_slice(JAN)
    assert jan_slice.citations == ["m_old"]

    # in March, only the new dose survives — the old one has been superseded
    mar_slice = p.valid_slice(MAR)
    assert mar_slice.citations == ["m_new"]
    assert "m_old" not in mar_slice.citations


def test_valid_slice_drops_an_unapproved_fact():
    approved = _med("m_ok", "Paracetamol", approved=True)
    unapproved = _med("m_draft", "Ibuprofen", approved=False)
    p = Patient(patient_id="p1", doctor_id="dr", facts=(approved, unapproved))

    assert p.valid_slice(FEB).citations == ["m_ok"]


def test_valid_slice_is_empty_when_nothing_current():
    future = _med("m_future", "Statin", eff=APR)
    p = Patient(patient_id="p1", doctor_id="dr", facts=(future,))
    s = p.valid_slice(FEB)
    assert s.is_empty is True
    assert s.count == 0


def test_valid_slice_records_as_of_instant():
    p = Patient(patient_id="p1", doctor_id="dr", facts=())
    assert p.valid_slice(FEB).as_of == FEB


def test_history_returns_retired_facts_newest_retired_first():
    first = _med("m1", "v1", eff=JAN, sup=FEB)
    second = _med("m2", "v2", eff=FEB, sup=MAR)
    current = _med("m3", "v3", eff=MAR)
    p = Patient(patient_id="p1", doctor_id="dr", facts=(first, second, current))

    hist = p.history(APR)
    assert [f.id for f in hist] == ["m2", "m1"]  # superseded MAR before FEB
    # the still-current fact is not history
    assert "m3" not in [f.id for f in hist]


def test_history_excludes_facts_not_yet_retired_as_of_now():
    retired = _med("m1", "v1", eff=JAN, sup=MAR)
    p = Patient(patient_id="p1", doctor_id="dr", facts=(retired,))
    # as of FEB the fact is still valid, not yet history
    assert p.history(FEB) == ()
    # as of APR it is history
    assert [f.id for f in p.history(APR)] == ["m1"]


def test_valid_slice_of_kind_filters_by_kind():
    med = _med("m1", "Amoxicillin")
    instr = Instruction(
        id="i1",
        validity=Validity(effective_from=JAN),
        summary="rest for a week",
        text="rest for a week",
    ).approve(by="dr", at=JAN)
    p = Patient(patient_id="p1", doctor_id="dr", facts=(med, instr))

    s = p.valid_slice(FEB)
    assert [f.id for f in s.of_kind(FactKind.MEDICATION)] == ["m1"]
    assert [f.id for f in s.of_kind(FactKind.INSTRUCTION)] == ["i1"]
    assert set(s.summaries) == {"Amoxicillin", "rest for a week"}


def test_with_fact_is_immutable():
    p0 = Patient(patient_id="p1", doctor_id="dr", facts=())
    p1 = p0.with_fact(_med("m1", "Amoxicillin"))
    assert p0.facts == ()  # original untouched
    assert [f.id for f in p1.facts] == ["m1"]


def test_valid_slice_is_a_frozen_snapshot():
    s = ValidSlice(as_of=FEB, facts=())
    assert s.model_config["frozen"] is True
