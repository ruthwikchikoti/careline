"""Enum vocabulary tests (RU-2).

Pin the closed vocabularies so a later rename or reordering is a deliberate,
reviewed change — every agent handoff speaks these and must not drift.
"""

import pytest

from careline.domain.enums import FactKind, ScopeCategory, TraceStatus, Verdict


def test_verdict_members_and_values():
    assert {v.value for v in Verdict} == {"answer", "clarify", "escalate"}


def test_verdict_is_str_enum():
    # str-backed so it serialises cleanly across the structured handoffs
    assert Verdict.ESCALATE == "escalate"
    assert isinstance(Verdict.ESCALATE.value, str)


@pytest.mark.parametrize(
    "category",
    ["in_scope", "out_of_scope", "cross_condition", "red_flag", "administrative"],
)
def test_scope_category_covers_safety_classes(category):
    assert category in {c.value for c in ScopeCategory}


def test_fact_kinds_present():
    assert {
        "medication",
        "instruction",
        "diagnosis",
        "observation",
        "allergy",
        "follow_up",
    } <= {k.value for k in FactKind}


def test_trace_status_members():
    assert {s.value for s in TraceStatus} == {"pass", "terminal", "skipped"}
