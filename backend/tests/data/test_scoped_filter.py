"""Structural isolation + query-builder tests (NG-5).

These pin that the scope always leads, that the valid-slice filter encodes the
half-open + approval predicate, and that history is its complement — all as plain
dict assertions (no DB needed).
"""

from datetime import datetime, timezone

from careline.adapters.mongo.filters import (
    history_filter,
    scoped_filter,
    valid_slice_filter,
)

NOW = datetime(2026, 6, 15, tzinfo=timezone.utc)


def test_scope_always_includes_doctor_id():
    f = scoped_filter(doctor_id="dr-X", patient_id="patient-A")
    assert f["doctor_id"] == "dr-X"
    assert f["patient_id"] == "patient-A"


def test_scope_can_narrow_to_tenant_only():
    f = scoped_filter(doctor_id="dr-X")
    assert f == {"doctor_id": "dr-X"}  # never wider than one tenant


def test_extra_predicates_cannot_remove_scope():
    f = scoped_filter(doctor_id="dr-X", patient_id="patient-A", kind="medication")
    assert f["doctor_id"] == "dr-X" and f["patient_id"] == "patient-A"
    assert f["kind"] == "medication"


def test_valid_slice_filter_encodes_half_open_and_approval():
    f = valid_slice_filter(doctor_id="dr-X", patient_id="patient-A", now=NOW)
    assert f["doctor_id"] == "dr-X" and f["patient_id"] == "patient-A"
    assert f["approved_by"] == {"$ne": None}              # doctor-approved only
    assert f["effective_from"] == {"$lte": NOW}           # has taken effect
    assert f["$or"] == [{"superseded_at": None}, {"superseded_at": {"$gt": NOW}}]


def test_history_filter_is_the_complement():
    f = history_filter(doctor_id="dr-X", patient_id="patient-A", now=NOW)
    assert f["superseded_at"] == {"$ne": None, "$lte": NOW}  # already retired
