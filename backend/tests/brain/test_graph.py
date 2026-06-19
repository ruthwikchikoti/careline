"""Multi-node LangGraph tests (RU-4).

Prove the compiled graph has the expected agent nodes, routes to all three
terminals via the conditional edge on the verdict, and early-exits to escalate
when a rail trips or a reasoning port is unavailable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from careline.adapters.orchestration.graph import (
    AGENT_NODES,
    TERMINAL_NODES,
    build_question_graph,
)
from careline.domain.enums import FactKind, ScopeCategory, Verdict
from careline.domain.model.call_session import CallSession
from careline.domain.model.fact import Medication
from careline.domain.model.patient import Patient
from careline.domain.model.proposal import ClassifierProposal, VerificationResult
from careline.domain.model.temporal import Validity
from careline.domain.ports.reasoning import Reasoner, ReasonerUnavailable, Verifier
from careline.domain.thresholds import Thresholds

_NOW = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
_PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def _patient(*, facts=None):
    if facts is None:
        facts = (
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
    return Patient(patient_id="patient-A", doctor_id="dr-X", facts=tuple(facts))


def _session():
    return CallSession(call_id="c1", patient_id="patient-A", doctor_id="dr-X", max_clarify_turns=2)


def _answerable():
    return ClassifierProposal.answerable(
        "Yes, continue Paracetamol 500mg twice daily.",
        citations=("med-1",),
        confidence=0.95,
        risk=0.1,
        scope=ScopeCategory.IN_SCOPE,
    )


def _graph(reasoner, verifier):
    return build_question_graph(
        reasoner=reasoner, verifier=verifier, thresholds=Thresholds(risk_ceiling=0.85)
    )


# -- structure ---------------------------------------------------------------


def test_graph_compiles_with_expected_nodes():
    g = _graph(_FakeReasoner(_answerable()), _FakeVerifier(VerificationResult.affirm(confidence=0.9)))
    nodes = set(g.compiled.get_graph().nodes)
    for name in (*AGENT_NODES, *TERMINAL_NODES):
        assert name in nodes


def test_mermaid_renders():
    g = _graph(_FakeReasoner(_answerable()), _FakeVerifier(VerificationResult.affirm(confidence=0.9)))
    diagram = g.mermaid()
    assert "triage" in diagram and "gate" in diagram


# -- all three routes reachable ----------------------------------------------


def test_route_answer():
    g = _graph(_FakeReasoner(_answerable()), _FakeVerifier(VerificationResult.affirm(confidence=0.92)))
    state = g.final_state(
        question="Should I still take Paracetamol?", patient=_patient(), now=_NOW, session=_session()
    )
    assert state["decision"].verdict is Verdict.ANSWER
    assert state["route"] == "answer"


def test_route_clarify():
    g = _graph(
        _FakeReasoner(ClassifierProposal.not_answerable(ScopeCategory.IN_SCOPE, rationale="unclear")),
        _FakeVerifier(),
    )
    state = g.final_state(
        question="What painkiller am I on?", patient=_patient(), now=_NOW, session=_session()
    )
    assert state["decision"].verdict is Verdict.CLARIFY
    assert state["route"] == "clarify"


def test_route_escalate_via_red_flag_early_exit():
    g = _graph(_FakeReasoner(_answerable()), _FakeVerifier(VerificationResult.affirm(confidence=0.9)))
    state = g.final_state(
        question="I have chest pain right now", patient=_patient(), now=_NOW, session=_session()
    )
    assert state["decision"].verdict is Verdict.ESCALATE
    assert state["decision"].scope is ScopeCategory.RED_FLAG
    assert state["route"] == "escalate"


def test_reasoner_unavailable_early_exits_to_escalate():
    g = _graph(_FakeReasoner(raises=True), _FakeVerifier())
    decision = g.run_question(
        question="What is my dose?", patient=_patient(), now=_NOW, session=_session()
    )
    assert decision.verdict is Verdict.ESCALATE
    assert decision.trace.terminal_step.name == "reasoner"
