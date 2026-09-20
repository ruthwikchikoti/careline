"""Consent VO tests (NG-3).

Consent is the DPDP gate and it is fail-closed: only an explicit, un-withdrawn grant
is active; withdrawal is irreversible and audit-preserving.
"""

from datetime import datetime, timezone

import pytest

from careline.domain.model.consent import Consent

T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 2, tzinfo=timezone.utc)
PURPOSE = "post-consultation follow-up answering"


def test_default_consent_is_inactive():
    c = Consent(subject_id="patient-A", purpose=PURPOSE)
    assert c.is_active is False  # absence of a grant reads as refusal


def test_grant_is_active():
    c = Consent.grant(subject_id="patient-A", purpose=PURPOSE, at=T0)
    assert c.is_active is True
    assert c.granted_at == T0


def test_withdraw_makes_inactive_but_keeps_grant_for_audit():
    c = Consent.grant(subject_id="patient-A", purpose=PURPOSE, at=T0).withdraw(T1)
    assert c.is_active is False
    assert c.granted is True and c.granted_at == T0  # audit trail retained
    assert c.withdrawn_at == T1


def test_cannot_withdraw_inactive_consent():
    c = Consent(subject_id="patient-A", purpose=PURPOSE)
    with pytest.raises(ValueError):
        c.withdraw(T1)


def test_withdrawal_cannot_precede_grant():
    c = Consent.grant(subject_id="patient-A", purpose=PURPOSE, at=T1)
    with pytest.raises(ValueError):
        c.withdraw(T0)


def test_consent_is_frozen():
    c = Consent.grant(subject_id="patient-A", purpose=PURPOSE, at=T0)
    with pytest.raises(Exception):
        c.granted = False  # type: ignore[misc]
