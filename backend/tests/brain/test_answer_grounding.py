"""Deterministic answer-text grounding check (gate chain, final invariants).

Provenance: final red-team finding
``answer-text-not-checked-against-cited-fact-superseded-content`` (2026-10-08).
At v6 the citation veto worked on ids only, so a confident reasoner could say
"Take Metformin 1000mg twice daily." — the SUPERSEDED dose — while citing the
CURRENT fact's id (``med-new``: 500mg); with an affirming LLM verifier both
the Brain and the graph ANSWERED it. The only protection was the LLM verifier.

Rules pinned here (shared by Brain and graph via ``run_gate_chain``):

1. Every dose / strength / frequency / number token (``1000mg``, ``500 mg``,
   ``2 weeks``, ``twice daily``, ``3 times``) and every drug / medication name
   in the answer text must appear in the text of at least one CITED fact that
   is in the current valid slice. Units, spacing, case and number words are
   normalised ("500 milligrams" == "500mg", "two times" == "twice").
2. A token found only in a superseded / not-yet-valid fact of this patient
   (or in no fact) forces CLARIFY with the emergency line — ESCALATE when a
   danger concept is present or the clarify budget is spent. Never ANSWER.
3. A faithful answer (the doctor's phrasing, or a unit/number-word paraphrase
   of it) still ANSWERS.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from careline.adapters.orchestration.graph import build_question_graph
from careline.domain.brain.brain import Brain
from careline.domain.enums import ScopeCategory, TraceStatus, Verdict
from careline.domain.gates.chain import EMERGENCY_LINE, GateContext, run_gate_chain
from careline.domain.gates.grounding import answer_tokens, ungrounded_tokens
from careline.domain.model.call_session import CallSession
from careline.domain.model.fact import Allergy, FollowUp, Instruction, Medication
from careline.domain.model.patient import Patient
from careline.domain.model.proposal import ClassifierProposal, VerificationResult
from careline.domain.model.temporal import Validity
from careline.domain.ports.reasoning import Reasoner, Verifier
from careline.domain.thresholds import Thresholds

_NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
_PAST = datetime(2026, 9, 1, tzinfo=timezone.utc)
_SWITCH = datetime(2026, 10, 1, tzinfo=timezone.utc)
_FUTURE = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _metformin_patient(*, same_id: bool = False) -> Patient:
    """The red-team repro: med-old 1000mg superseded 2026-10-01, med-new 500mg current."""
    old = Medication(
        id="med-1" if same_id else "med-old", summary="Metformin 1000mg twice daily.",
        name="Metformin", dose="1000mg", frequency="twice daily",
        validity=Validity(effective_from=_PAST, superseded_at=_SWITCH),
        approved_by="dr-X", approved_at=_PAST,
    )
    new = Medication(
        id="med-1" if same_id else "med-new", summary="Metformin 500mg twice daily.",
        name="Metformin", dose="500mg", frequency="twice daily",
        validity=Validity(effective_from=_SWITCH), approved_by="dr-X", approved_at=_SWITCH,
    )
    return Patient(patient_id="patient-A", doctor_id="dr-X", facts=(old, new))


def _rich_patient() -> Patient:
    common = dict(validity=Validity(effective_from=_PAST), approved_by="dr-X", approved_at=_PAST)
    return Patient(
        patient_id="patient-B",
        doctor_id="dr-X",
        facts=(
            Medication(id="med-1", summary="Paracetamol 500mg twice daily for post-op pain.",
                       name="Paracetamol", dose="500mg", frequency="twice daily", **common),
            Instruction(id="ins-1", summary="Soft diet for 2 weeks post-surgery; avoid spicy food.",
                        text="Soft diet for 2 weeks post-surgery; avoid spicy food.", **common),
            Allergy(id="alg-1", summary="Penicillin allergy — causes rash.",
                    substance="Penicillin", reaction="rash", **common),
            FollowUp(id="fu-1", summary="Post-op review on 22 October 2026 with Dr. Asha.",
                     scheduled_for=datetime(2026, 10, 22, tzinfo=timezone.utc), **common),
            # Retired antibiotic: stopped before now.
            Medication(id="med-0", summary="Amoxicillin 250mg three times a day for 5 days.",
                       name="Amoxicillin", dose="250mg", frequency="three times a day",
                       validity=Validity(effective_from=_PAST, superseded_at=_SWITCH),
                       approved_by="dr-X", approved_at=_PAST),
            # Not yet valid: starts next month.
            Medication(id="med-9", summary="Atorvastatin 20mg once daily at night.",
                       name="Atorvastatin", dose="20mg", frequency="once daily",
                       validity=Validity(effective_from=_FUTURE),
                       approved_by="dr-X", approved_at=_PAST),
        ),
    )


class _Says(Reasoner):
    def __init__(self, text: str, citations: tuple[str, ...]) -> None:
        self._t, self._c = text, citations

    def propose(self, *, question, context):
        return ClassifierProposal.answerable(self._t, citations=self._c, confidence=0.99,
                                             risk=0.05)


class _Affirm(Verifier):
    def verify(self, *, question, proposal, context):
        return VerificationResult.affirm(confidence=0.99)


def _session(budget: int = 2, patient_id: str = "patient-A") -> CallSession:
    return CallSession(call_id="g", patient_id=patient_id, doctor_id="dr-X",
                       max_clarify_turns=budget)


def _run_both(text, citations, *, patient, question="What dose should I take?", budget=2):
    out = []
    for factory in (Brain, build_question_graph):
        d = factory(
            reasoner=_Says(text, tuple(citations)), verifier=_Affirm(),
            thresholds=Thresholds(risk_ceiling=0.85),
        ).run_question(question=question, patient=patient, now=_NOW,
                       session=_session(budget, patient.patient_id))
        out.append(d)
    b, g = out
    assert (b.verdict, b.scope, b.answer_text, b.escalation_reason, tuple(b.citations)) == (
        g.verdict, g.scope, g.answer_text, g.escalation_reason, tuple(g.citations)
    ), "Brain and graph disagree on the grounding check"
    return b


# ---------------------------------------------------------------------------
# 1. The red-team repro — superseded dose behind the current fact's id
# ---------------------------------------------------------------------------
def test_superseded_dose_behind_current_citation_is_never_answered():
    d = _run_both("Take Metformin 1000mg twice daily.", ("med-new",),
                  patient=_metformin_patient(),
                  question="What dose of metformin should I take?")
    assert d.verdict is not Verdict.ANSWER
    assert d.verdict is Verdict.CLARIFY
    assert EMERGENCY_LINE in (d.answer_text or "")
    assert "1000" not in (d.answer_text or "")


def test_superseded_dose_with_a_reused_id_is_never_answered():
    # The superseded and current versions share one id: the id-only veto passes.
    d = _run_both("Take Metformin 1000mg twice daily.", ("med-1",),
                  patient=_metformin_patient(same_id=True),
                  question="What dose of metformin should I take?")
    assert d.verdict is Verdict.CLARIFY


def test_superseded_dose_escalates_when_clarify_budget_spent():
    d = _run_both("Take Metformin 1000mg twice daily.", ("med-new",),
                  patient=_metformin_patient(), budget=0,
                  question="What dose of metformin should I take?")
    assert d.verdict is Verdict.ESCALATE


def test_current_dose_still_answers():
    d = _run_both("Take Metformin 500mg twice daily.", ("med-new",),
                  patient=_metformin_patient(),
                  question="What dose of metformin should I take?")
    assert d.verdict is Verdict.ANSWER
    assert d.citations == ["med-new"]


@pytest.mark.parametrize(
    "text",
    [
        "Metformin 500mg twice daily.",
        "Take metformin 500 mg twice daily.",
        "Take 500 MG of Metformin, two times a day.",
        "Your dose is 500 milligrams of metformin twice a day.",
        "Take metformin 500mg 2 times daily.",
        "Take metformin 500-mg twice daily.",
    ],
)
def test_faithful_paraphrases_still_answer(text):
    d = _run_both(text, ("med-new",), patient=_metformin_patient(),
                  question="What dose of metformin should I take?")
    assert d.verdict is Verdict.ANSWER, text


@pytest.mark.parametrize(
    ("text", "citations"),
    [
        # wrong strength / frequency / duration
        ("Take Metformin 500mg three times daily.", ("med-new",)),
        ("Take Metformin 500mg once daily.", ("med-new",)),
        ("Take Metformin 850mg twice daily.", ("med-new",)),
        ("Take Metformin 500mg twice daily for 3 months.", ("med-new",)),
        ("Take 2 tablets of Metformin 500mg twice daily.", ("med-new",)),
        # a drug that is in no fact
        ("Take Glimepiride 500mg twice daily.", ("med-new",)),
        ("Take Metformin 500mg twice daily with ibuprofen.", ("med-new",)),
    ],
)
def test_ungrounded_dose_frequency_or_drug_is_never_answered(text, citations):
    d = _run_both(text, citations, patient=_metformin_patient())
    assert d.verdict is Verdict.CLARIFY, text


@pytest.mark.parametrize(
    ("text", "citations"),
    [
        # Retired (superseded) drug, cited fact is current.
        ("Continue Amoxicillin 250mg three times a day.", ("med-1",)),
        # Not-yet-valid drug and dose.
        ("Take Atorvastatin 20mg once daily at night.", ("med-1",)),
        # Drug/dose from a VALID but UNCITED fact.
        ("Paracetamol 500mg twice daily.", ("ins-1",)),
        # Duration from an uncited fact.
        ("Soft diet for 5 days.", ("ins-1",)),
        ("Your review is on 25 October 2026.", ("fu-1",)),
    ],
)
def test_retired_future_or_uncited_content_is_never_answered(text, citations):
    d = _run_both(text, citations, patient=_rich_patient(), question="What should I do?")
    assert d.verdict is not Verdict.ANSWER, text
    assert d.verdict is Verdict.CLARIFY


@pytest.mark.parametrize(
    ("text", "citations"),
    [
        ("Soft diet for 2 weeks post-surgery; avoid spicy food.", ("ins-1",)),
        ("Stay on a soft diet for two weeks after surgery and avoid spicy food.", ("ins-1",)),
        ("Your review is on 22 October 2026 with Dr. Asha.", ("fu-1",)),
        ("Paracetamol 500mg twice daily. You are allergic to penicillin.", ("med-1", "alg-1")),
        ("Take paracetamol 500mg twice daily. If it's an emergency call 112.", ("med-1",)),
    ],
)
def test_grounded_answers_from_the_cited_facts_answer(text, citations):
    d = _run_both(text, citations, patient=_rich_patient(), question="What should I do?")
    assert d.verdict is Verdict.ANSWER, text


def test_grounding_veto_escalates_with_a_danger_concept():
    patient = _metformin_patient()
    ctx = GateContext(
        question="My uncle used to have chest pain, what dose of metformin do I take?",
        proposal=ClassifierProposal.answerable(
            "Take Metformin 1000mg twice daily.", citations=("med-new",),
            confidence=0.99, risk=0.05, scope=ScopeCategory.IN_SCOPE,
        ),
        verification=VerificationResult.affirm(confidence=0.99),
        valid_slice=patient.valid_slice(_NOW),
        thresholds=Thresholds(risk_ceiling=0.85),
        now=_NOW,
        call_session=_session(),
    )
    assert run_gate_chain(ctx).verdict is Verdict.ESCALATE


def test_grounding_check_is_recorded_and_names_the_retired_fact():
    patient = _metformin_patient()
    ctx = GateContext(
        question="What dose of metformin should I take?",
        proposal=ClassifierProposal.answerable(
            "Take Metformin 1000mg twice daily.", citations=("med-new",),
            confidence=0.99, risk=0.05,
        ),
        verification=VerificationResult.affirm(confidence=0.99),
        valid_slice=patient.valid_slice(_NOW),
        non_current_facts=tuple(f for f in patient.facts if not f.is_current(_NOW)),
        thresholds=Thresholds(risk_ceiling=0.85),
        now=_NOW,
        call_session=_session(),
    )
    d = run_gate_chain(ctx)
    assert d.verdict is Verdict.CLARIFY
    steps = [s for s in d.trace.steps if s.name == "answer_grounding"]
    assert steps and steps[-1].status is TraceStatus.TERMINAL
    assert "1000 mg" in steps[-1].detail and "med-old" in steps[-1].detail


def test_grounding_check_passes_and_is_recorded_on_a_faithful_answer():
    patient = _metformin_patient()
    ctx = GateContext(
        question="What dose of metformin should I take?",
        proposal=ClassifierProposal.answerable(
            "Take Metformin 500mg twice daily.", citations=("med-new",),
            confidence=0.99, risk=0.05,
        ),
        verification=VerificationResult.affirm(confidence=0.99),
        valid_slice=patient.valid_slice(_NOW),
        thresholds=Thresholds(risk_ceiling=0.85),
        now=_NOW,
        call_session=_session(),
    )
    d = run_gate_chain(ctx)
    assert d.verdict is Verdict.ANSWER
    assert any(s.name == "answer_grounding" and s.status is TraceStatus.PASS
               for s in d.trace.steps)


# ---------------------------------------------------------------------------
# 2. The pure extractor
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Take 1000mg", {"1000 mg"}),
        ("500 MG", {"500 mg"}),
        ("500 milligrams", {"500 mg"}),
        ("1,000 mg", {"1000 mg"}),
        ("0.50 ml", {"0.5 ml"}),
        ("for 2 weeks", {"2 week"}),
        ("for two weeks", {"2 week"}),
        ("twice daily", {"2 times"}),
        ("3 times a day", {"3 times"}),
        ("three times a day", {"3 times"}),
        ("every 8 hours", {"8 hour"}),
        ("once at night", {"1 times"}),
        ("Paracetamol", {"drug:paracetamol"}),
        ("acetaminophen", {"drug:paracetamol"}),
        ("Levofloxacin", {"drug:levofloxacin"}),    # suffix family, not in the lexicon
        ("call 112 now", set()),                    # emergency numbers are not claims
        ("one of your medicines", set()),           # bare number words are not claims
    ],
)
def test_answer_tokens_normalise(text, expected):
    assert set(answer_tokens(text)) == expected


def test_ungrounded_tokens_reports_only_the_unsupported():
    patient = _metformin_patient()
    cited = [f for f in patient.valid_slice(_NOW).facts if f.id == "med-new"]
    assert ungrounded_tokens("Take Metformin 1000mg twice daily.", cited) == ["1000 mg"]
    assert ungrounded_tokens("Take Metformin 500mg twice daily.", cited) == []
