"""Decision + ReasoningTrace tests (RU-2).

These pin the frozen handoff shape and its invariants: the three named factories
produce well-formed verdicts, an ANSWER always carries text, an ESCALATE always
carries a reason, confidence/risk stay bounded, and the trace stays explainable.
"""

import pytest
from pydantic import ValidationError

from careline.domain.enums import ScopeCategory, TraceStatus, Verdict
from careline.domain.model.decision import Decision, ReasoningTrace, TraceStep


# -- trace -------------------------------------------------------------------


def test_trace_records_steps_in_order():
    trace = (
        ReasoningTrace()
        .record("red_flag_rail", TraceStatus.PASS, spec_section="§5.1")
        .record("scope_gate", TraceStatus.PASS)
    )
    assert [s.name for s in trace.steps] == ["red_flag_rail", "scope_gate"]
    assert trace.terminated is False
    assert trace.terminal_step is None


def test_trace_detects_terminal_step():
    trace = ReasoningTrace().record(
        "red_flag_rail", TraceStatus.TERMINAL, detail="chest pain tripwire"
    )
    assert trace.terminated is True
    assert trace.terminal_step is not None
    assert trace.terminal_step.name == "red_flag_rail"


def test_trace_step_is_frozen():
    step = TraceStep(name="gate", status=TraceStatus.PASS)
    with pytest.raises(ValidationError):
        step.name = "mutated"


# -- factories ---------------------------------------------------------------


def test_answer_factory_sets_verdict_and_text():
    d = Decision.answer("Yes, paracetamol is fine.", confidence=0.9, citations=["f1"])
    assert d.verdict is Verdict.ANSWER
    assert d.answer_text == "Yes, paracetamol is fine."
    assert d.scope is ScopeCategory.IN_SCOPE
    assert d.citations == ["f1"]
    assert d.is_terminal_escalation is False


def test_answer_requires_non_empty_text():
    with pytest.raises(ValueError):
        Decision.answer("   ", confidence=0.9)


def test_escalate_factory_sets_reason():
    d = Decision.escalate("red-flag: chest pain", scope=ScopeCategory.RED_FLAG, risk=1.0)
    assert d.verdict is Verdict.ESCALATE
    assert d.escalation_reason == "red-flag: chest pain"
    assert d.is_terminal_escalation is True


def test_escalate_requires_reason():
    with pytest.raises(ValueError):
        Decision.escalate("")


def test_clarify_factory():
    d = Decision.clarify("Which medication do you mean?")
    assert d.verdict is Verdict.CLARIFY
    assert d.answer_text == "Which medication do you mean?"


# -- invariants --------------------------------------------------------------


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_confidence_must_be_a_probability(bad):
    with pytest.raises(ValidationError):
        Decision.answer("ok", confidence=bad)


@pytest.mark.parametrize("bad", [-0.5, 2.0])
def test_risk_must_be_a_probability(bad):
    with pytest.raises(ValidationError):
        Decision.escalate("reason", risk=bad)


def test_decision_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        Decision(verdict=Verdict.CLARIFY, surprise="nope")
