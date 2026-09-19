"""SR-2 — the keyless heuristic twins: grounding, declining, and the veto."""

from __future__ import annotations

from datetime import datetime, timezone

from careline.adapters.llm.heuristic import HeuristicReasoner, HeuristicVerifier
from careline.domain.enums import ScopeCategory
from careline.domain.model.fact import Diagnosis, Instruction, Medication
from careline.domain.model.patient import ValidSlice
from careline.domain.model.temporal import Validity

NOW = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
_PAST = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _approved(fact):
    return fact.approve("dr-1", _PAST)


def _med() -> Medication:
    return _approved(
        Medication(
            id="med-1",
            validity=Validity(effective_from=_PAST),
            summary="Take 500mg paracetamol every 6 hours as needed for pain.",
            name="paracetamol",
            dose="500mg",
            frequency="every 6 hours",
        )
    )


def _wound() -> Instruction:
    return _approved(
        Instruction(
            id="ins-1",
            validity=Validity(effective_from=_PAST),
            summary="Keep the surgical wound dry for seven days.",
            text="keep wound dry seven days",
        )
    )


def slice_of(*facts) -> ValidSlice:
    return ValidSlice(as_of=NOW, facts=tuple(facts))


class TestHeuristicReasoner:
    def test_grounds_an_answer_in_a_matching_fact(self):
        proposal = HeuristicReasoner().propose(
            question="what is my paracetamol dose?", context=slice_of(_med())
        )
        assert proposal.is_answerable
        assert proposal.scope is ScopeCategory.IN_SCOPE
        assert proposal.citations == ("med-1",)
        # Surfaces the doctor-approved phrasing verbatim — not invented text.
        assert "500mg paracetamol" in proposal.candidate_answer
        assert proposal.confidence > 0.5

    def test_empty_slice_declines(self):
        proposal = HeuristicReasoner().propose(
            question="what is my dose?", context=slice_of()
        )
        assert not proposal.is_answerable
        assert proposal.scope is ScopeCategory.OUT_OF_SCOPE
        assert proposal.confidence == 0.0

    def test_unrelated_question_declines_out_of_scope(self):
        proposal = HeuristicReasoner().propose(
            question="can I drink alcohol on holiday in spain?", context=slice_of(_med())
        )
        assert not proposal.is_answerable
        assert proposal.scope is ScopeCategory.OUT_OF_SCOPE

    def test_administrative_question_is_classified_admin(self):
        proposal = HeuristicReasoner().propose(
            question="can I reschedule my appointment?", context=slice_of(_med())
        )
        assert proposal.scope is ScopeCategory.ADMINISTRATIVE

    def test_cross_condition_question_is_not_merged(self):
        d1 = _approved(
            Diagnosis(
                id="dx-1",
                validity=Validity(effective_from=_PAST),
                summary="Diagnosed with hypertension.",
                condition="hypertension",
            )
        )
        d2 = _approved(
            Diagnosis(
                id="dx-2",
                validity=Validity(effective_from=_PAST),
                summary="Diagnosed with diabetes.",
                condition="diabetes",
            )
        )
        proposal = HeuristicReasoner().propose(
            question="how do my hypertension and diabetes interact?",
            context=slice_of(d1, d2),
        )
        assert not proposal.is_answerable
        assert proposal.scope is ScopeCategory.CROSS_CONDITION

    def test_gibberish_question_declines(self):
        proposal = HeuristicReasoner().propose(question="?? a", context=slice_of(_med()))
        assert not proposal.is_answerable


class TestHeuristicVerifier:
    def test_affirms_a_grounded_proposal(self):
        ctx = slice_of(_med())
        proposal = HeuristicReasoner().propose(question="paracetamol dose?", context=ctx)
        result = HeuristicVerifier().verify(question="paracetamol dose?", proposal=proposal, context=ctx)
        assert result.supported is True
        assert result.unsupported_claims == ()

    def test_vetoes_a_non_answerable_proposal(self):
        from careline.domain.model.proposal import ClassifierProposal

        result = HeuristicVerifier().verify(
            question="x",
            proposal=ClassifierProposal.not_answerable(ScopeCategory.OUT_OF_SCOPE),
            context=slice_of(_med()),
        )
        assert result.supported is False

    def test_vetoes_a_citation_not_in_the_valid_slice(self):
        # A candidate that cites a fact absent from the slice = superseded/leaked fact.
        from careline.domain.model.proposal import ClassifierProposal

        proposal = ClassifierProposal.answerable(
            "Take 500mg paracetamol every 6 hours.",
            citations=["med-GONE"],
            confidence=0.9,
        )
        result = HeuristicVerifier().verify(
            question="dose?", proposal=proposal, context=slice_of(_med())
        )
        assert result.supported is False
        assert any("med-GONE" in c for c in result.unsupported_claims)

    def test_vetoes_an_ungrounded_claim(self):
        # Candidate cites a valid fact but adds a substantive claim it doesn't support.
        from careline.domain.model.proposal import ClassifierProposal

        proposal = ClassifierProposal.answerable(
            "Take 500mg paracetamol and also start amoxicillin antibiotics.",
            citations=["med-1"],
            confidence=0.9,
        )
        result = HeuristicVerifier().verify(
            question="meds?", proposal=proposal, context=slice_of(_med())
        )
        assert result.supported is False
        assert "amoxicillin" in result.unsupported_claims
