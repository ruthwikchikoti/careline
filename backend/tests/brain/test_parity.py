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
from careline.domain.model.fact import Instruction, Medication, Observation
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


# ---------------------------------------------------------------------------
# Parity under retrieval narrowing (RU-7)
# ---------------------------------------------------------------------------
#
# The plain parity battery above uses single-fact patients, so retrieval never
# narrows (total <= DEFAULT_K). These cases use a *rich* record (> DEFAULT_K facts)
# so the retriever actually trims the slice, AND use context-sensitive fakes so the
# verifier's context matters. This is the regression guard for the parity break where
# the graph fed the verifier the narrowed grounding while the Brain fed it the full
# slice — both must feed the verifier the FULL valid slice.


_VETO_FACT_ID = "obs-z-veto"


class _ContextAwareReasoner(Reasoner):
    """Answerable iff the medication is in the context it is given."""

    def propose(self, *, question, context):
        if any(f.id == "med-1" for f in context.facts):
            return _answerable()
        return ClassifierProposal.not_answerable(
            ScopeCategory.IN_SCOPE, rationale="medication not in grounding"
        )


class _ContextAwareVerifier(Verifier):
    """Vetoes iff the contraindication observation is visible in its context.

    With the verifier wired to the FULL valid slice, this fact is always visible even
    when retrieval trims it from the reasoner's grounding — so both engines veto and
    escalate identically. If a verifier were ever fed the narrowed grounding instead,
    it could miss this fact and (wrongly) affirm — which this test would catch as a
    parity break.
    """

    def verify(self, *, question, proposal, context):
        if any(f.id == _VETO_FACT_ID for f in context.facts):
            return VerificationResult.veto(unsupported_claims=("contraindication",))
        return VerificationResult.affirm(confidence=0.95)


def _rich_patient() -> Patient:
    """A > DEFAULT_K record: 1 med + 1 instruction (always kept) + 6 observations.

    The contraindication observation has the highest id, so with all six observations
    tying at score 0 it sorts last and is trimmed from the reasoner's grounding — but
    never from the verifier's full-slice view.
    """
    facts = [
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
        Instruction(
            id="instr-1",
            kind=FactKind.INSTRUCTION,
            validity=Validity(effective_from=_PAST),
            summary="Rest and recover for two weeks.",
            text="Rest and recover for two weeks.",
            approved_by="dr-X",
            approved_at=_PAST,
        ),
    ]
    obs_ids = ["obs-1", "obs-2", "obs-3", "obs-4", "obs-5", _VETO_FACT_ID]
    for oid in obs_ids:
        facts.append(
            Observation(
                id=oid,
                kind=FactKind.OBSERVATION,
                validity=Validity(effective_from=_PAST),
                summary=f"Routine reading {oid}.",
                metric="reading",
                value="1",
                unit="x",
                approved_by="dr-X",
                approved_at=_PAST,
            )
        )
    return Patient(patient_id="patient-A", doctor_id="dr-X", facts=tuple(facts))


def test_graph_matches_brain_under_narrowing():
    """Graph and Brain agree on a rich record where retrieval narrows the grounding."""
    reasoner = _ContextAwareReasoner()
    verifier = _ContextAwareVerifier()
    brain = Brain(reasoner=reasoner, verifier=verifier, thresholds=_THRESHOLDS)
    graph = build_question_graph(reasoner=reasoner, verifier=verifier, thresholds=_THRESHOLDS)

    q = "should I take my paracetamol"
    # Budget exhausted so the verifier veto deterministically resolves to ESCALATE (not a
    # clarify nudge) — what we care about is that BOTH engines take the same branch.
    bd = brain.run_question(
        question=q, patient=_rich_patient(), now=_NOW, session=_session(exhausted=True)
    )
    gd = graph.run_question(
        question=q, patient=_rich_patient(), now=_NOW, session=_session(exhausted=True)
    )

    # Both must escalate (verifier sees the contraindication via the full slice) — and agree.
    assert bd.verdict is gd.verdict
    assert bd.verdict.value == "escalate"
    assert bd.scope == gd.scope
    assert list(bd.citations) == list(gd.citations)


def test_narrowing_never_drops_actionable_facts_but_trims_observations():
    """Safety-aware retrieval: medication + instruction always kept; an observation trimmed."""
    from careline.domain.retrieval import retrieve_relevant

    patient = _rich_patient()
    result = retrieve_relevant(
        question="should I take my paracetamol", valid_slice=patient.valid_slice(_NOW)
    )
    grounded_ids = {f.id for f in result.grounding.facts}
    assert result.narrowed is True
    assert "med-1" in grounded_ids  # actionable: never dropped
    assert "instr-1" in grounded_ids  # actionable: never dropped
    assert _VETO_FACT_ID not in grounded_ids  # lowest-ranked observation: trimmed
    # Whatever survives is a strict subset of the valid slice (no fabricated facts).
    assert grounded_ids.issubset({f.id for f in patient.valid_slice(_NOW).facts})
