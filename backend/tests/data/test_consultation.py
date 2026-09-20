"""Consultation aggregate tests (NG-3).

Pin the two safety invariants: no processing without active consent, and facts can
only be promoted via an explicit approve() transition off a consented draft.
"""

from datetime import datetime, timezone

import pytest

from careline.domain.model.consent import Consent
from careline.domain.model.consultation import Consultation
from careline.domain.model.fact import Instruction
from careline.domain.model.temporal import Validity

T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _consent() -> Consent:
    return Consent.grant(subject_id="patient-A", purpose="follow-up", at=T0)


def _draft(*, consent: Consent | None) -> Consultation:
    return Consultation(
        consultation_id="c1",
        doctor_id="dr-X",
        patient_id="patient-A",
        created_at=T0,
        transcript="patient reports mild pain",
        consent=consent,
    )


def _fact() -> Instruction:
    return Instruction(
        id="i1",
        validity=Validity(effective_from=T0),
        summary="rest for a week",
        text="rest for a week",
    ).approve(by="dr-X", at=T0)


def test_no_consent_is_not_processable():
    assert _draft(consent=None).is_processable is False


def test_withdrawn_consent_is_not_processable():
    withdrawn = _consent().withdraw(datetime(2026, 6, 2, tzinfo=timezone.utc))
    assert _draft(consent=withdrawn).is_processable is False


def test_active_consent_is_processable():
    assert _draft(consent=_consent()).is_processable is True


def test_with_facts_is_immutable():
    c0 = _draft(consent=_consent())
    c1 = c0.with_facts((_fact(),))
    assert c0.facts == ()
    assert [f.id for f in c1.facts] == ["i1"]


def test_approve_requires_consent():
    with pytest.raises(ValueError):
        _draft(consent=None).approve()


def test_approve_flips_draft_to_approved():
    c = _draft(consent=_consent()).approve()
    assert c.is_approved is True
    assert c.status == "approved"


def test_cannot_re_approve():
    c = _draft(consent=_consent()).approve()
    with pytest.raises(ValueError):
        c.approve()
