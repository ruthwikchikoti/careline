"""Graph ↔ Brain parity (RU-5).

The single most important test of the orchestration layer: for the same inputs,
the multi-node LangGraph and the headless Brain must return the *identical*
verdict (and, for answers, the same text / scope / citations). This is what lets
the multi-agent presentation exist without ever drifting from the verified
decision core — adding the graph cannot change a safety decision.

Each scenario is run through both engines and their decisions are compared
field-by-field across all three routes (ANSWER / CLARIFY / ESCALATE) and every
early-exit (red-flag, cross-condition, reasoner/verifier unavailable, empty
slice, verifier veto).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from careline.adapters.orchestration.graph import build_question_graph
from careline.domain.brain.brain import Brain
from careline.domain.enums import FactKind, ScopeCategory
from careline.domain.model.call_session import CallSession
from careline.domain.model.fact import Medication
from careline.domain.model.patient import Patient
from careline.domain.model.proposal import ClassifierProposal, VerificationResult
from careline.domain.model.temporal import Validity
from careline.domain.ports.reasoning import Reasoner, ReasonerUnavailable, Verifier
from careline.domain.thresholds import Thresholds

_NOW = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
_PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)
_THRESHOLDS = Thresholds(risk_ceiling=0.85)


class _FakeReasoner(Reasoner):
    def __init__(self, proposal=None, *, raises=False):
        self._p, self._raises = proposal, raises

    def propose(self, *, question, context):
        if self._raises:
            raise ReasonerUnavailable("offline")
        return self._p


class _FakeVerifier(Verifier):
    def __init__(self, result=None, *, raises=False):
        self._r, self._raises = result, raises

    def verify(self, *, question, proposal, context):
        if self._raises:
            raise ReasonerUnavailable("offline")
        return self._r


def _patient(*, empty=False):
    facts = () if empty else (
        Medication(
            id="med-1",
            kind=FactKind.MEDICATION,
            validity=Validity(effective_from=_PAST),
            summary="Paracetamol 500mg twice daily.",
            name="Paracetamol",
            dose="500mg",
            frequency="twice daily",
            approved_by="dr-X",
            approved_at=_PAST,
        ),
    )
    return Patient(patient_id="patient-A", doctor_id="dr-X", facts=facts)


def _session(*, exhausted=False):
    s = CallSession(call_id="c1", patient_id="patient-A", doctor_id="dr-X", max_clarify_turns=2)
    if exhausted:
        s.clarify_count = s.max_clarify_turns
    return s


def _answerable():
    return ClassifierProposal.answerable(
        "Yes, continue Paracetamol 500mg twice daily.",
        citations=("med-1",),
        confidence=0.95,
        risk=0.1,
        scope=ScopeCategory.IN_SCOPE,
    )


# Each scenario: (id, question, reasoner, verifier, patient_kwargs, session_kwargs)
_SCENARIOS = [
    (
        "red_flag",
        "I have chest pain right now",
        _FakeReasoner(_answerable()),
        _FakeVerifier(VerificationResult.affirm(confidence=0.9)),
        {}, {},
    ),
    (
        "cross_condition",
        "Can I take metformin for diabetes after my surgery?",
        _FakeReasoner(_answerable()),
        _FakeVerifier(VerificationResult.affirm(confidence=0.9)),
        {}, {},
    ),
    (
        "answer_happy_path",
        "Should I still take Paracetamol?",
        _FakeReasoner(_answerable()),
        _FakeVerifier(VerificationResult.affirm(confidence=0.92)),
        {}, {},
    ),
    (
        "clarify_not_answerable",
        "What painkiller am I on?",
        _FakeReasoner(ClassifierProposal.not_answerable(ScopeCategory.IN_SCOPE, rationale="unclear")),
        _FakeVerifier(),
        {}, {},
    ),
    (
        "reasoner_unavailable",
        "What is my dose?",
        _FakeReasoner(raises=True),
        _FakeVerifier(),
        {}, {},
    ),
    (
        "verifier_unavailable",
        "Should I still take Paracetamol?",
        _FakeReasoner(_answerable()),
        _FakeVerifier(raises=True),
        {}, {},
    ),
    (
        "empty_slice",
        "What medication am I on?",
        _FakeReasoner(ClassifierProposal.not_answerable(ScopeCategory.IN_SCOPE, rationale="no facts")),
        _FakeVerifier(),
        {"empty": True}, {},
    ),
    (
        "verifier_veto_budget_exhausted",
        "What diet should I follow?",
        _FakeReasoner(_answerable()),
        _FakeVerifier(VerificationResult.veto(unsupported_claims=("contradiction",))),
        {}, {"exhausted": True},
    ),
]


@pytest.mark.parametrize(
    "name,question,reasoner,verifier,pkw,skw",
    _SCENARIOS,
    ids=[s[0] for s in _SCENARIOS],
)
def test_graph_matches_brain(name, question, reasoner, verifier, pkw, skw):
    brain = Brain(reasoner=reasoner, verifier=verifier, thresholds=_THRESHOLDS)
    graph = build_question_graph(reasoner=reasoner, verifier=verifier, thresholds=_THRESHOLDS)

    brain_decision = brain.run_question(
        question=question, patient=_patient(**pkw), now=_NOW, session=_session(**skw)
    )
    graph_decision = graph.run_question(
        question=question, patient=_patient(**pkw), now=_NOW, session=_session(**skw)
    )

    assert graph_decision.verdict is brain_decision.verdict, (
        f"{name}: graph={graph_decision.verdict} brain={brain_decision.verdict}"
    )
    assert graph_decision.answer_text == brain_decision.answer_text
    assert graph_decision.scope == brain_decision.scope
    assert list(graph_decision.citations) == list(brain_decision.citations)


def test_every_route_is_represented():
    """Guard: the parity battery actually exercises all three verdicts."""
    verdicts = set()
    for _name, question, reasoner, verifier, pkw, skw in _SCENARIOS:
        brain = Brain(reasoner=reasoner, verifier=verifier, thresholds=_THRESHOLDS)
        verdicts.add(
            brain.run_question(
                question=question, patient=_patient(**pkw), now=_NOW, session=_session(**skw)
            ).verdict.value
        )
    assert verdicts == {"answer", "clarify", "escalate"}
