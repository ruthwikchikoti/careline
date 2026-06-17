"""Brain orchestrator tests (RU-3).

These exercise the headless decision pipeline directly, with fake reasoning
ports so the Brain's *orchestration* is tested in isolation from any reasoner
internals: the pre-LLM rails fire before the reasoner, the verifier runs only
when there is a candidate, every unavailable dependency fails closed to
ESCALATE, and a clean in-scope turn reaches ANSWER through the gate chain.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from careline.domain.brain.brain import Brain
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


# -- fakes for the reasoning ports -------------------------------------------


class FakeReasoner(Reasoner):
    def __init__(self, proposal: ClassifierProposal | None = None, *, raises: bool = False):
        self._proposal = proposal
        self._raises = raises
        self.calls = 0

    def propose(self, *, question, context):
        self.calls += 1
        if self._raises:
            raise ReasonerUnavailable("offline")
        return self._proposal


class FakeVerifier(Verifier):
    def __init__(self, result: VerificationResult | None = None, *, raises: bool = False):
        self._result = result
        self._raises = raises
        self.calls = 0

    def verify(self, *, question, proposal, context):
        self.calls += 1
        if self._raises:
            raise ReasonerUnavailable("offline")
        return self._result


def _patient(*, facts=None) -> Patient:
    if facts is None:
        facts = (
            Medication(
                id="med-1",
                kind=FactKind.MEDICATION,
                validity=Validity(effective_from=_PAST),
                summary="Paracetamol 500mg twice daily for pain.",
                name="Paracetamol",
                dose="500mg",
                frequency="twice daily",
                approved_by="dr-X",
                approved_at=_PAST,
            ),
        )
    return Patient(patient_id="patient-A", doctor_id="dr-X", facts=tuple(facts))


def _session() -> CallSession:
    return CallSession(call_id="c1", patient_id="patient-A", doctor_id="dr-X", max_clarify_turns=2)


def _answerable() -> ClassifierProposal:
    return ClassifierProposal.answerable(
        "Yes, continue Paracetamol 500mg twice daily.",
        citations=("med-1",),
        confidence=0.95,
        risk=0.1,
        scope=ScopeCategory.IN_SCOPE,
    )


def _brain(reasoner, verifier, *, risk_ceiling=0.85) -> Brain:
    return Brain(reasoner=reasoner, verifier=verifier, thresholds=Thresholds(risk_ceiling=risk_ceiling))


# -- rails fire pre-LLM ------------------------------------------------------


def test_red_flag_escalates_before_reasoner_runs():
    reasoner = FakeReasoner(_answerable())
    verifier = FakeVerifier(VerificationResult.affirm(confidence=0.9))
    brain = _brain(reasoner, verifier)

    decision = brain.run_question(
        question="I have chest pain since this morning", patient=_patient(), now=_NOW, session=_session()
    )

    assert decision.verdict is Verdict.ESCALATE
    assert decision.scope is ScopeCategory.RED_FLAG
    assert reasoner.calls == 0, "rail must short-circuit before the LLM"
    assert decision.trace.terminal_step.name == "red_flag_rail"


def test_multi_condition_escalates_before_reasoner():
    reasoner = FakeReasoner(_answerable())
    brain = _brain(reasoner, FakeVerifier())

    decision = brain.run_question(
        question="Can I take metformin for my diabetes after my surgery?",
        patient=_patient(),
        now=_NOW,
        session=_session(),
    )

    assert decision.verdict is Verdict.ESCALATE
    assert decision.scope is ScopeCategory.CROSS_CONDITION
    assert reasoner.calls == 0


# -- happy path --------------------------------------------------------------


def test_in_scope_confident_verified_answers():
    reasoner = FakeReasoner(_answerable())
    verifier = FakeVerifier(VerificationResult.affirm(confidence=0.92))
    brain = _brain(reasoner, verifier)

    decision = brain.run_question(
        question="Should I still take Paracetamol?", patient=_patient(), now=_NOW, session=_session()
    )

    assert decision.verdict is Verdict.ANSWER
    assert "Paracetamol" in (decision.answer_text or "")
    assert "med-1" in decision.citations
    assert verifier.calls == 1


# -- fail-closed on unavailable dependencies ---------------------------------


def test_reasoner_unavailable_fails_closed():
    brain = _brain(FakeReasoner(raises=True), FakeVerifier())
    decision = brain.run_question(
        question="What is my dose?", patient=_patient(), now=_NOW, session=_session()
    )
    assert decision.verdict is Verdict.ESCALATE
    assert decision.trace.terminal_step.name == "reasoner"


def test_verifier_unavailable_fails_closed():
    reasoner = FakeReasoner(_answerable())
    brain = _brain(reasoner, FakeVerifier(raises=True))
    decision = brain.run_question(
        question="Should I still take Paracetamol?", patient=_patient(), now=_NOW, session=_session()
    )
    assert decision.verdict is Verdict.ESCALATE
    assert decision.trace.terminal_step.name == "verifier"


# -- lazy verifier -----------------------------------------------------------


def test_verifier_skipped_when_not_answerable():
    reasoner = FakeReasoner(
        ClassifierProposal.not_answerable(ScopeCategory.IN_SCOPE, rationale="nothing grounded")
    )
    verifier = FakeVerifier(VerificationResult.affirm(confidence=0.9))
    brain = _brain(reasoner, verifier)

    decision = brain.run_question(
        question="What painkiller am I on?", patient=_patient(), now=_NOW, session=_session()
    )

    assert verifier.calls == 0, "verifier must not run without a candidate to check"
    assert decision.verdict in (Verdict.CLARIFY, Verdict.ESCALATE)


# -- clarify-then-escalate ---------------------------------------------------


def test_not_answerable_clarifies_then_escalates_on_budget():
    reasoner = FakeReasoner(
        ClassifierProposal.not_answerable(ScopeCategory.IN_SCOPE, rationale="unclear")
    )
    brain = _brain(reasoner, FakeVerifier())

    fresh = _session()
    assert brain.run_question(
        question="What painkiller am I on?", patient=_patient(), now=_NOW, session=fresh
    ).verdict is Verdict.CLARIFY

    exhausted = _session()
    exhausted.clarify_count = exhausted.max_clarify_turns
    assert brain.run_question(
        question="What painkiller am I on?", patient=_patient(), now=_NOW, session=exhausted
    ).verdict is Verdict.ESCALATE


# -- empty slice (e.g. cross-patient) escalates ------------------------------


def test_empty_valid_slice_escalates():
    reasoner = FakeReasoner(
        ClassifierProposal.not_answerable(ScopeCategory.IN_SCOPE, rationale="no facts")
    )
    brain = _brain(reasoner, FakeVerifier())

    decision = brain.run_question(
        question="What medication am I on?",
        patient=_patient(facts=()),
        now=_NOW,
        session=_session(),
    )
    assert decision.verdict is Verdict.ESCALATE


# -- the Brain does not mutate the session it reads --------------------------


def test_brain_does_not_advance_session():
    reasoner = FakeReasoner(_answerable())
    verifier = FakeVerifier(VerificationResult.affirm(confidence=0.9))
    brain = _brain(reasoner, verifier)
    session = _session()

    brain.run_question(
        question="Should I still take Paracetamol?", patient=_patient(), now=_NOW, session=session
    )

    assert session.turn_count == 0, "turn accounting belongs to the caller, not the Brain"
    assert session.clarify_count == 0
