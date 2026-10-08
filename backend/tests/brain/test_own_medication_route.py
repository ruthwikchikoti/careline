"""Own-medication route: a question about the patient's OWN medicine is never
redirected away (found by testing the live keyless demo).

At v8 a question naming a drug from this patient's record that the keyless
HeuristicReasoner could not ground ("How often can I take paracetamol?",
"Can I still take amoxicillin?" on a stopped drug, "What dose of metformin
should I take?") was labelled out-of-scope and ended in the scope gate's
CLARIFY redirect ("I can only help with the care your doctor approved ...
contact the clinic"). The doctor was never told, although the README's formal
problem says an ungroundable clinical ask ESCALATES.

The shared scope gate now never redirects an out-of-scope question that names
a medication from ANY of this patient's facts (current or not; brand -> generic
via the grounding lexicon). Like gate 4's unanswerable in-scope proposal, it
asks once to rephrase while the clarify budget lasts and ESCALATES once it is
spent; the one-shot surfaces (web demo, patient portal) run with a zero budget,
so the live examples ESCALATE at once. A drug that is NOT in this patient's
record (another patient's, a spouse's, general knowledge) keeps the redirect,
and the patient-facing text never names a drug or a fact. Brain and graph alike.

Owner: Priyanshu (scope ``safety``).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from careline.adapters.llm.heuristic import HeuristicReasoner, HeuristicVerifier
from careline.adapters.orchestration.graph import build_question_graph
from careline.domain.brain.brain import Brain
from careline.domain.enums import FactKind, ScopeCategory, Verdict
from careline.domain.gates.chain import GateContext, run_gate_chain
from careline.domain.gates.grounding import non_current_facts
from careline.domain.model.call_session import CallSession
from careline.domain.model.fact import Instruction, Medication
from careline.domain.model.patient import Patient
from careline.domain.model.proposal import ClassifierProposal, VerificationResult
from careline.domain.model.temporal import Validity

_NOW = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
_PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)
_SUPERSEDED = datetime(2026, 3, 1, tzinfo=timezone.utc)

_ROUTE_STEP = "own_medication_route"


def _med(fid, name, dose, freq, *, doctor="dr-X", superseded=False):
    return Medication(
        id=fid,
        kind=FactKind.MEDICATION,
        validity=Validity(
            effective_from=_PAST, superseded_at=_SUPERSEDED if superseded else None
        ),
        summary=f"{name} {dose} {freq}{' (discontinued)' if superseded else ''}.",
        name=name,
        dose=dose,
        frequency=freq,
        approved_by=doctor,
        approved_at=_PAST,
    )


def _diet(fid="instr-1", *, doctor="dr-X"):
    text = "Soft diet for 2 weeks post-surgery. Avoid spicy food."
    return Instruction(
        id=fid, kind=FactKind.INSTRUCTION, validity=Validity(effective_from=_PAST),
        summary=text, text=text, approved_by=doctor, approved_at=_PAST,
    )


def _demo_patient() -> Patient:
    """Mirrors the live demo patient: current paracetamol, STOPPED amoxicillin."""
    return Patient(
        patient_id="patient-A",
        doctor_id="dr-X",
        facts=(
            _med("med-1", "Paracetamol", "500mg", "twice daily"),
            _med("med-2", "Amoxicillin", "250mg", "thrice daily", superseded=True),
            _diet(),
        ),
    )


def _metformin_patient() -> Patient:
    return Patient(
        patient_id="patient-M",
        doctor_id="dr-X",
        facts=(_med("med-m", "Metformin", "500mg", "twice daily"), _diet("instr-m")),
    )


def _other_patient() -> Patient:
    """Another patient of the same doctor — their warfarin must never route A."""
    return Patient(
        patient_id="patient-B",
        doctor_id="dr-X",
        facts=(_med("med-b", "Warfarin", "5mg", "once daily"),),
    )


def _session(pid="patient-A", *, budget=2):
    return CallSession(call_id="c", patient_id=pid, doctor_id="dr-X", max_clarify_turns=budget)


def _run(question, patient, *, budget=2):
    """(brain decision, graph decision) with the keyless heuristic twins."""
    out = []
    for factory in (Brain, build_question_graph):
        engine = factory(reasoner=HeuristicReasoner(), verifier=HeuristicVerifier())
        out.append(engine.run_question(
            question=question, patient=patient, now=_NOW,
            session=_session(patient.patient_id, budget=budget),
        ))
    return out


def _steps(decision):
    return [s.name for s in decision.trace.steps]


def _text(decision) -> str:
    return " ".join(filter(None, (decision.answer_text, decision.escalation_reason)))


def _assert_parity(brain, graph):
    assert graph.verdict is brain.verdict
    assert graph.scope == brain.scope
    assert graph.answer_text == brain.answer_text
    assert graph.escalation_reason == brain.escalation_reason
    assert list(graph.citations) == list(brain.citations)


# -- the three live-demo examples escalate ------------------------------------


_LIVE_EXAMPLES = [
    ("How often can I take paracetamol?", _demo_patient, "paracetamol"),
    ("Can I still take amoxicillin?", _demo_patient, "amoxicillin"),
    ("What dose of metformin should I take?", _metformin_patient, "metformin"),
]


@pytest.mark.parametrize(("question", "patient_fn", "drug"), _LIVE_EXAMPLES)
def test_own_medication_question_escalates_in_both_engines(question, patient_fn, drug):
    """Zero clarify budget (the web demo and the patient portal): ESCALATE."""
    brain, graph = _run(question, patient_fn(), budget=0)
    _assert_parity(brain, graph)
    for d in (brain, graph):
        assert d.verdict is Verdict.ESCALATE, (question, d.verdict, _text(d))
        assert d.scope is ScopeCategory.IN_SCOPE  # not a red flag
        assert d.risk == pytest.approx(0.7)
        assert "medication" in d.escalation_reason.lower()
        assert "doctor" in d.escalation_reason.lower()
        assert _ROUTE_STEP in _steps(d)
        step = next(s for s in d.trace.steps if s.name == _ROUTE_STEP)
        assert drug in (step.detail or "").lower()
        assert d.citations == []


@pytest.mark.parametrize(("question", "patient_fn", "drug"), _LIVE_EXAMPLES)
def test_own_medication_question_with_budget_asks_to_rephrase_not_redirects(
    question, patient_fn, drug
):
    """Clarify budget left (a voice call): one rephrase CLARIFY that promises the
    doctor — never the 'contact the clinic' redirect; no drug named in the text."""
    brain, graph = _run(question, patient_fn(), budget=2)
    _assert_parity(brain, graph)
    for d in (brain, graph):
        assert d.verdict is Verdict.CLARIFY
        assert d.scope is ScopeCategory.IN_SCOPE
        assert _ROUTE_STEP in _steps(d)
        assert "contact the clinic" not in d.answer_text
        assert "doctor" in d.answer_text and "medication" in d.answer_text
        assert drug not in d.answer_text.lower()


@pytest.mark.parametrize(("question", "patient_fn", "drug"), _LIVE_EXAMPLES)
def test_own_medication_question_escalates_once_the_budget_is_spent(question, patient_fn, drug):
    """Same session, asked again after the rephrase turn: ESCALATE."""
    patient = patient_fn()
    for factory in (Brain, build_question_graph):
        engine = factory(reasoner=HeuristicReasoner(), verifier=HeuristicVerifier())
        session = _session(patient.patient_id, budget=1)
        first = engine.run_question(question=question, patient=patient, now=_NOW, session=session)
        assert first.verdict is Verdict.CLARIFY
        session.record_clarify()
        second = engine.run_question(question=question, patient=patient, now=_NOW, session=session)
        assert second.verdict is Verdict.ESCALATE
        assert second.scope is ScopeCategory.IN_SCOPE


@pytest.mark.parametrize("budget", [0, 2])
def test_discontinued_drug_question_never_cites_or_repeats_the_retired_fact(budget):
    brain, graph = _run("Can I still take amoxicillin?", _demo_patient(), budget=budget)
    for d in (brain, graph):
        assert d.verdict is (Verdict.ESCALATE if budget == 0 else Verdict.CLARIFY)
        assert "med-2" not in d.citations
        text = _text(d)
        assert "250" not in text and "thrice" not in text.lower()
        assert "med-2" not in text
        for s in d.trace.steps:
            assert "med-2" not in (s.detail or "")
            assert "250" not in (s.detail or "")


# -- drugs NOT in this patient's record are unchanged -------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Can I take my wife's insulin?",
        "What is ibuprofen?",
        "Can I take warfarin?",   # patient-B's drug, never patient-A's
    ],
)
@pytest.mark.parametrize("budget", [0, 2])
def test_drug_not_in_this_patients_record_keeps_the_redirect(question, budget):
    brain, graph = _run(question, _demo_patient(), budget=budget)
    _assert_parity(brain, graph)
    for d in (brain, graph):
        assert _ROUTE_STEP not in _steps(d)
        assert d.verdict is Verdict.CLARIFY, (question, d.verdict, _text(d))
        assert d.scope is ScopeCategory.OUT_OF_SCOPE


@pytest.mark.parametrize("budget", [0, 2])
def test_another_patients_drug_never_triggers_the_route_nor_appears_in_text(budget):
    other = _other_patient()
    assert any(getattr(f, "name", "") == "Warfarin" for f in other.facts)
    brain, graph = _run("Can I take warfarin with my dinner?", _demo_patient(), budget=budget)
    for d in (brain, graph):
        assert _ROUTE_STEP not in _steps(d)
        assert "warfarin" not in _text(d).lower()
        assert "5mg" not in _text(d)


# -- benign / emergency / grounded answers unchanged --------------------------


@pytest.mark.parametrize("budget", [0, 2])
def test_benign_drug_free_out_of_scope_question_still_redirects(budget):
    brain, graph = _run("What's the weather like today?", _demo_patient(), budget=budget)
    _assert_parity(brain, graph)
    for d in (brain, graph):
        assert d.verdict is Verdict.CLARIFY
        assert d.scope is ScopeCategory.OUT_OF_SCOPE
        assert _ROUTE_STEP not in _steps(d)


@pytest.mark.parametrize("budget", [0, 2])
def test_grounded_diet_question_still_answers(budget):
    brain, graph = _run("Can I eat spicy food tonight?", _demo_patient(), budget=budget)
    _assert_parity(brain, graph)
    for d in (brain, graph):
        assert d.verdict is Verdict.ANSWER
        assert _ROUTE_STEP not in _steps(d)


@pytest.mark.parametrize(
    "question",
    ["I have crushing chest pain", "I took paracetamol and now I have crushing chest pain"],
)
@pytest.mark.parametrize("budget", [0, 2])
def test_emergency_still_escalates_as_red_flag(question, budget):
    brain, graph = _run(question, _demo_patient(), budget=budget)
    for d in (brain, graph):
        assert d.verdict is Verdict.ESCALATE
        assert d.scope is ScopeCategory.RED_FLAG
        assert _ROUTE_STEP not in _steps(d)


# -- the shared gate chain, directly ------------------------------------------


def _ctx(question, patient, *, scope=ScopeCategory.OUT_OF_SCOPE, budget=0):
    return GateContext(
        question=question,
        proposal=ClassifierProposal.not_answerable(scope, rationale="not grounded"),
        verification=VerificationResult.affirm(confidence=0.9),
        valid_slice=patient.valid_slice(_NOW),
        non_current_facts=non_current_facts(patient, _NOW),
        now=_NOW,
        call_session=_session(patient.patient_id, budget=budget),
    )


@pytest.mark.parametrize(
    "question",
    [
        "Is Crocin okay before bed?",        # brand -> paracetamol (current)
        "Should I restart my AMOXICILLIN?",  # stopped drug, any case
        "paracetamol??",
    ],
)
def test_gate_chain_routes_own_drug_including_brand_and_retired(question):
    d = run_gate_chain(_ctx(question, _demo_patient()))
    assert d.verdict is Verdict.ESCALATE
    assert d.scope is ScopeCategory.IN_SCOPE
    assert _ROUTE_STEP in _steps(d)


@pytest.mark.parametrize("budget", [0, 2])
def test_gate_chain_text_names_no_drug_and_no_fact(budget):
    d = run_gate_chain(_ctx("Is Crocin okay before bed?", _demo_patient(), budget=budget))
    text = _text(d).lower()
    for word in ("paracetamol", "crocin", "amoxicillin", "500", "med-1", "med-2"):
        assert word not in text


def test_gate_chain_without_a_session_fails_closed_to_escalate():
    patient = _demo_patient()
    ctx = _ctx("How often can I take paracetamol?", patient)
    ctx.call_session = None
    d = run_gate_chain(ctx)
    assert d.verdict is Verdict.ESCALATE and _ROUTE_STEP in _steps(d)


@pytest.mark.parametrize(
    "question", ["Can I take my wife's insulin?", "What is ibuprofen?", "Tell me a joke"]
)
@pytest.mark.parametrize("budget", [0, 2])
def test_gate_chain_leaves_foreign_or_drug_free_questions_alone(question, budget):
    d = run_gate_chain(_ctx(question, _demo_patient(), budget=budget))
    assert d.verdict is Verdict.CLARIFY
    assert d.scope is ScopeCategory.OUT_OF_SCOPE
    assert _ROUTE_STEP not in _steps(d)


def test_gate_chain_route_is_scoped_to_this_patient_only():
    """Patient B's warfarin is in B's record, not A's: A's question is redirected,
    B's is routed to B's doctor."""
    q = "Can I take warfarin?"
    a = run_gate_chain(_ctx(q, _demo_patient()))
    b = run_gate_chain(_ctx(q, _other_patient()))
    assert a.verdict is Verdict.CLARIFY and _ROUTE_STEP not in _steps(a)
    assert b.verdict is Verdict.ESCALATE and _ROUTE_STEP in _steps(b)


def test_gate_chain_red_flag_scope_wins_over_the_route():
    d = run_gate_chain(
        _ctx("paracetamol overdose", _demo_patient(), scope=ScopeCategory.RED_FLAG)
    )
    assert d.verdict is Verdict.ESCALATE
    assert d.scope is ScopeCategory.RED_FLAG
    assert _ROUTE_STEP not in _steps(d)
