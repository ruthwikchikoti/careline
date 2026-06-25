"""QuestionService integration — red-flag, clarify budget, escalation (VI-6).

Exercises the full offline spine through :class:`QuestionService` rather than
calling the gate chain directly.  Complements the T1–T8 bake-off in
``test_bakeoff_safety.py``.

Owner: Vinay (scope ``eval``).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from careline.adapters.llm.heuristic import HeuristicReasoner, HeuristicVerifier
from careline.adapters.telephony.stub import TelephonyStub
from careline.domain.enums import FactKind, ScopeCategory, Verdict
from careline.domain.model.call_session import CallSession
from careline.domain.model.fact import Instruction, Medication
from careline.domain.model.patient import Patient
from careline.domain.model.temporal import Validity
from careline.domain.thresholds import Thresholds
from careline.services.question_service import QuestionService

_NOW = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
_PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _service() -> tuple[QuestionService, TelephonyStub]:
    telephony = TelephonyStub()
    svc = QuestionService(
        reasoner=HeuristicReasoner(),
        verifier=HeuristicVerifier(),
        telephony=telephony,
        thresholds=Thresholds(risk_ceiling=0.85),
    )
    return svc, telephony


def _session(clarify_count: int = 0) -> CallSession:
    return CallSession(
        call_id="call-svc-001",
        patient_id="patient-A",
        doctor_id="dr-X",
        clarify_count=clarify_count,
        max_clarify_turns=2,
    )


def _patient() -> Patient:
    return Patient(
        patient_id="patient-A",
        doctor_id="dr-X",
        facts=(
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
            Instruction(
                id="instr-1",
                kind=FactKind.INSTRUCTION,
                validity=Validity(effective_from=_PAST),
                summary="Soft diet for 2 weeks post-surgery. Avoid spicy food.",
                text="Soft diet for 2 weeks post-surgery. Avoid spicy food.",
                approved_by="dr-X",
                approved_at=_PAST,
            ),
        ),
    )


class TestRedFlagEscalation:
    def test_chest_pain_escalates_without_llm(self):
        svc, telephony = _service()
        decision = svc.run_question(
            question="I have chest pain and feel dizzy",
            patient=_patient(),
            session=_session(),
            now=_NOW,
        )
        assert decision.verdict is Verdict.ESCALATE
        assert decision.scope is ScopeCategory.RED_FLAG
        assert telephony.last() is not None
        assert "chest pain" in telephony.last().reason.lower()  # type: ignore[union-attr]


class TestHappyPath:
    def test_in_scope_question_answers(self):
        svc, telephony = _service()
        decision = svc.run_question(
            question="paracetamol dose?",
            patient=_patient(),
            session=_session(),
            now=_NOW,
        )
        assert decision.verdict is Verdict.ANSWER
        assert "Paracetamol" in (decision.answer_text or "")
        assert telephony.escalations == []


class TestOutOfScope:
    def test_out_of_scope_redirects_without_escalating(self):
        # Out-of-scope (non-clinical / not in the approved care plan) is a polite
        # redirect, NOT a doctor escalation — even with the clarify budget spent.
        # Escalating non-clinical noise would flood the queue; emergencies still
        # escalate via the red-flag rail.
        svc, telephony = _service()
        session = _session(clarify_count=2)  # budget already exhausted
        decision = svc.run_question(
            question="What about the thing?",
            patient=_patient(),
            session=session,
            now=_NOW,
        )
        assert decision.verdict is Verdict.CLARIFY
        assert telephony.escalations == []


class TestMultiConditionTripwire:
    def test_diabetes_and_surgery_escalates_pre_llm(self):
        svc, telephony = _service()
        decision = svc.run_question(
            question="Can I eat sweets post-surgery given my diabetes?",
            patient=_patient(),
            session=_session(),
            now=_NOW,
        )
        assert decision.verdict is Verdict.ESCALATE
        assert decision.scope is ScopeCategory.CROSS_CONDITION
        assert telephony.last() is not None
