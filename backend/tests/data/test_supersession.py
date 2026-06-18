"""§B.6 supersession plan tests (NG-5).

The plan is the safety-critical decision: a new fact retires the currently-valid
fact of the same clinical identity (seamless half-open handover), free-text kinds are
add-only, and a new fact takes effect at the handover instant.
"""

from datetime import datetime, timezone

from careline.adapters.mongo.supersession import plan_supersession
from careline.domain.model.fact import Instruction, Medication
from careline.domain.model.temporal import Validity

PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _med(id_, name, *, eff=PAST) -> Medication:
    return Medication(
        id=id_,
        validity=Validity(effective_from=eff),
        summary=name,
        name=name,
        approved_by="dr-X",
        approved_at=eff,
    ).model_copy()


def test_new_medication_supersedes_same_drug():
    current = (_med("m-old", "Amoxicillin"),)
    incoming = (_med("m-new", "Amoxicillin"),)
    plan = plan_supersession(current=current, incoming=incoming, now=NOW)
    assert plan.to_close == ("m-old",)
    assert len(plan.to_insert) == 1


def test_different_drug_does_not_supersede():
    current = (_med("m-old", "Amoxicillin"),)
    incoming = (_med("m-new", "Paracetamol"),)
    plan = plan_supersession(current=current, incoming=incoming, now=NOW)
    assert plan.to_close == ()


def test_inserted_fact_takes_effect_at_handover_instant():
    incoming = (_med("m-new", "Amoxicillin", eff=PAST),)
    plan = plan_supersession(current=(), incoming=incoming, now=NOW)
    inserted = plan.to_insert[0]
    assert inserted.validity.effective_from == NOW
    assert inserted.validity.superseded_at is None


def test_instruction_is_add_only():
    # Free-text instructions have no safe natural key — never auto-superseded
    # (a stale-vs-new contradiction is left for the gate chain to escalate).
    def _instr(id_, text):
        return Instruction(
            id=id_, validity=Validity(effective_from=PAST), summary=text, text=text,
            approved_by="dr-X", approved_at=PAST,
        )

    current = (_instr("i-old", "liquid diet"),)
    incoming = (_instr("i-new", "soft diet"),)
    plan = plan_supersession(current=current, incoming=incoming, now=NOW)
    assert plan.to_close == ()
    assert len(plan.to_insert) == 1


def test_does_not_close_a_fact_that_starts_at_or_after_now():
    current = (_med("m-old", "Amoxicillin", eff=NOW),)  # starts exactly at the cut
    incoming = (_med("m-new", "Amoxicillin"),)
    plan = plan_supersession(current=current, incoming=incoming, now=NOW)
    assert plan.to_close == ()  # cannot cleanly close a not-yet-elapsed interval
