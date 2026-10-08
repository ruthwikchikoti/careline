"""QuestionService ↔ Brain ↔ graph parity (three-way).

``QuestionService`` is the path the live API actually serves. It used to carry
a *third* copy of the decision pipeline that had already drifted from the
Brain (no small-talk rail, no retrieval narrowing); it now delegates to the
Brain, and these tests keep it pinned: for the same inputs, the headless
Brain, the service's inline path, and the service's graph path must return
the identical verdict, text, scope, and citations.

The ``small_talk`` scenario is the historical drift case: pre-delegation the
service answered it from the reasoner while the Brain nudged conversationally.
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
from careline.services.question_service import QuestionService

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


def _patient():
    return Patient(
        patient_id="patient-A",
        doctor_id="dr-X",
        facts=(
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
        ),
    )


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


# (id, question, reasoner, verifier)
_SCENARIOS = [
    (
        "answer_happy_path",
        "Should I still take Paracetamol?",
        _FakeReasoner(_answerable()),
        _FakeVerifier(VerificationResult.affirm(confidence=0.92)),
    ),
    (
        "red_flag",
        "I have chest pain right now",
        _FakeReasoner(_answerable()),
        _FakeVerifier(VerificationResult.affirm(confidence=0.9)),
    ),
    (
        "reasoner_unavailable",
        "What is my dose?",
        _FakeReasoner(raises=True),
        _FakeVerifier(),
    ),
    (
        "verifier_veto",
        "What diet should I follow?",
        _FakeReasoner(_answerable()),
        _FakeVerifier(VerificationResult.veto(unsupported_claims=("contradiction",))),
    ),
    (
        "not_answerable",
        "What painkiller am I on?",
        _FakeReasoner(
            ClassifierProposal.not_answerable(ScopeCategory.IN_SCOPE, rationale="unclear")
        ),
        _FakeVerifier(),
    ),
    # The historical drift case: the Brain's conversational rail nudges; the
    # old duplicated pipeline answered it from the reasoner instead.
    (
        "small_talk",
        "hi there!",
        _FakeReasoner(_answerable()),
        _FakeVerifier(VerificationResult.affirm(confidence=0.9)),
    ),
]


@pytest.mark.parametrize(
    "name,question,reasoner,verifier", _SCENARIOS, ids=[s[0] for s in _SCENARIOS]
)
def test_service_inline_and_graph_match_brain(name, question, reasoner, verifier):
    brain = Brain(reasoner=reasoner, verifier=verifier, thresholds=_THRESHOLDS)
    inline = QuestionService(reasoner=reasoner, verifier=verifier, thresholds=_THRESHOLDS)
    graph_svc = QuestionService(
        graph=build_question_graph(reasoner=reasoner, verifier=verifier, thresholds=_THRESHOLDS),
        thresholds=_THRESHOLDS,
    )

    brain_d = brain.run_question(question=question, patient=_patient(), now=_NOW, session=_session())
    inline_d = inline.run_question(question=question, patient=_patient(), now=_NOW, session=_session())
    graph_d = graph_svc.run_question(question=question, patient=_patient(), now=_NOW, session=_session())

    for label, d in (("inline", inline_d), ("graph", graph_d)):
        assert d.verdict is brain_d.verdict, (
            f"{name}: service-{label}={d.verdict} brain={brain_d.verdict}"
        )
        assert d.answer_text == brain_d.answer_text, f"{name}: service-{label} text drifted"
        assert d.scope == brain_d.scope, f"{name}: service-{label} scope drifted"
        assert list(d.citations) == list(brain_d.citations), (
            f"{name}: service-{label} citations drifted"
        )


def test_small_talk_nudges_instead_of_answering():
    """The drift fix, pinned directly: greetings never reach the reasoner's answer."""
    reasoner = _FakeReasoner(_answerable())
    verifier = _FakeVerifier(VerificationResult.affirm(confidence=0.9))
    svc = QuestionService(reasoner=reasoner, verifier=verifier, thresholds=_THRESHOLDS)
    d = svc.run_question(question="good morning!", patient=_patient(), now=_NOW, session=_session())
    assert d.verdict.value == "clarify"
    assert "medicines" in (d.answer_text or "") or "help" in (d.answer_text or "")


@pytest.mark.parametrize(
    "name,question,reasoner,verifier", _SCENARIOS, ids=[s[0] for s in _SCENARIOS]
)
def test_both_service_paths_feed_the_online_monitor_identically(name, question, reasoner, verifier):
    """The monitor sees every served turn — graph path and inline path alike."""
    from careline.services import online_monitor
    from careline.services.online_monitor import DriftReference, OnlineMonitor

    seen = {}
    for label, svc in (
        ("inline", QuestionService(reasoner=reasoner, verifier=verifier, thresholds=_THRESHOLDS)),
        ("graph", QuestionService(
            graph=build_question_graph(reasoner=reasoner, verifier=verifier,
                                       thresholds=_THRESHOLDS),
            thresholds=_THRESHOLDS,
        )),
    ):
        online_monitor.reset_monitor(OnlineMonitor(
            window=10, judge_sample_rate=0.0,
            reference=DriftReference({"in_scope": 1.0}, frozenset(), 0.0, 1.0, 1),
        ))
        try:
            d = svc.run_question(question=question, patient=_patient(), now=_NOW,
                                 session=_session())
            snap = online_monitor.snapshot()
        finally:
            online_monitor.reset_monitor()
        assert snap["operational"]["requests_total"] == 1, f"{name}: {label} did not record"
        assert snap["output"]["verdict_counts"][d.verdict.value] == 1
        assert snap["operational"]["latency_ms_p50"] > 0.0
        seen[label] = snap["output"]["verdict_counts"]
    assert seen["inline"] == seen["graph"]
