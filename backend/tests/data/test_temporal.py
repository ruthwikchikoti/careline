"""Validity (half-open interval) tests (NG-1).

These pin the single source of temporal truth: the lower bound is inclusive, the
upper bound is exclusive, an open interval never ends, and supersession is a
fail-closed, non-destructive operation.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from careline.domain.model.temporal import Validity

JAN = datetime(2026, 1, 1)
FEB = datetime(2026, 2, 1)
MAR = datetime(2026, 3, 1)


# -- half-open membership ----------------------------------------------------


def test_lower_bound_is_inclusive():
    v = Validity(effective_from=JAN, superseded_at=MAR)
    assert v.is_valid_at(JAN) is True


def test_upper_bound_is_exclusive():
    v = Validity(effective_from=JAN, superseded_at=MAR)
    # the instant of supersession is already "no longer valid"
    assert v.is_valid_at(MAR) is False


def test_inside_the_interval_is_valid():
    v = Validity(effective_from=JAN, superseded_at=MAR)
    assert v.is_valid_at(FEB) is True


def test_before_effective_from_is_invalid():
    v = Validity(effective_from=FEB, superseded_at=MAR)
    assert v.is_valid_at(JAN) is False


def test_open_interval_never_ends():
    v = Validity(effective_from=JAN)
    assert v.is_open is True
    assert v.is_valid_at(JAN) is True
    assert v.is_valid_at(datetime(2099, 1, 1)) is True
    assert v.is_valid_at(datetime(2025, 1, 1)) is False


# -- construction invariants -------------------------------------------------


def test_superseded_at_must_be_after_effective_from():
    with pytest.raises(ValidationError):
        Validity(effective_from=MAR, superseded_at=JAN)


def test_superseded_at_equal_to_effective_from_is_rejected():
    with pytest.raises(ValidationError):
        Validity(effective_from=JAN, superseded_at=JAN)


def test_validity_is_frozen():
    v = Validity(effective_from=JAN)
    with pytest.raises(ValidationError):
        v.effective_from = FEB


# -- supersession ------------------------------------------------------------


def test_supersede_closes_the_interval_without_mutating_original():
    v = Validity(effective_from=JAN)
    closed = v.supersede(MAR)
    assert closed.superseded_at == MAR
    assert closed.is_valid_at(FEB) is True
    assert closed.is_valid_at(MAR) is False
    # original is untouched (frozen, non-destructive)
    assert v.is_open is True


def test_cannot_supersede_twice():
    v = Validity(effective_from=JAN, superseded_at=MAR)
    with pytest.raises(ValueError):
        v.supersede(datetime(2026, 4, 1))


def test_cannot_supersede_before_effective_from():
    v = Validity(effective_from=FEB)
    with pytest.raises(ValueError):
        v.supersede(JAN)
