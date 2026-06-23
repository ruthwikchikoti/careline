"""End-to-end integration of the assembled graph (RU-6).

Unlike the unit tests (which inject fake ports), this builds the graph the way the
app does — through ``build_default_graph()`` and the real offline heuristic twins —
and drives real questions through it. Proves the composition entry point wires a
working spine with no API key and no database, and that the safety routes hold with
the real reasoner/verifier rather than fakes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from careline.adapters.orchestration.graph import build_default_graph
from careline.domain.enums import FactKind, ScopeCategory, Verdict
from careline.domain.model.call_session import CallSession
from careline.domain.model.fact import Instruction, Medication
from careline.domain.model.patient import Patient
from careline.domain.model.temporal import Validity

_NOW = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
_PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def _session() -> CallSession:
    return CallSession(call_id="c1", patient_id="patient-A", doctor_id="dr-X", max_clarify_turns=2)


def test_default_graph_builds_offline_and_runs():
    graph = build_default_graph()  # keyless heuristic twins
    decision = graph.run_question(
        question="What diet should I follow?", patient=_patient(), now=_NOW, session=_session()
    )
    # The real heuristic spine must return a well-formed terminal decision.
    assert decision.verdict in (Verdict.ANSWER, Verdict.CLARIFY, Verdict.ESCALATE)
    assert decision.trace.steps, "a real run must leave an explainable trace"


def test_red_flag_escalates_through_real_spine():
    graph = build_default_graph()
    decision = graph.run_question(
        question="I have severe chest pain and difficulty breathing",
        patient=_patient(),
        now=_NOW,
        session=_session(),
    )
    assert decision.verdict is Verdict.ESCALATE
    assert decision.scope is ScopeCategory.RED_FLAG


def test_final_state_exposes_route_and_decision():
    graph = build_default_graph()
    state = graph.final_state(
        question="I have chest pain", patient=_patient(), now=_NOW, session=_session()
    )
    assert state["route"] == "escalate"
    assert state["decision"].verdict is Verdict.ESCALATE
