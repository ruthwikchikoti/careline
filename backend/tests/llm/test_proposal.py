"""SR-1 — the Reasoner/Verifier handoff models and their invariants."""

from __future__ import annotations

import pytest

from careline.domain.enums import ScopeCategory
from careline.domain.model.proposal import ClassifierProposal, VerificationResult


class TestClassifierProposal:
    def test_answerable_factory_builds_a_grounded_candidate(self):
        p = ClassifierProposal.answerable(
            "Take 500mg paracetamol every 6 hours.",
            citations=["fact-1"],
            confidence=0.9,
        )
        assert p.is_answerable
        assert p.scope is ScopeCategory.IN_SCOPE
        assert p.citations == ("fact-1",)

    def test_answerable_requires_non_empty_candidate(self):
        with pytest.raises(ValueError):
            ClassifierProposal.answerable("   ", citations=["f"], confidence=0.5)

    def test_answerable_requires_at_least_one_citation(self):
        # An answer must always trace to a fact — never to the model's prior.
        with pytest.raises(ValueError):
            ClassifierProposal.answerable("something", citations=[], confidence=0.5)

    def test_candidate_without_citations_is_not_answerable(self):
        # Even constructed directly, a candidate with no citations fails closed.
        p = ClassifierProposal(
            scope=ScopeCategory.IN_SCOPE,
            candidate_answer="freeform claim",
            citations=(),
            confidence=0.9,
        )
        assert p.is_answerable is False

    def test_not_answerable_pins_confidence_to_zero(self):
        p = ClassifierProposal.not_answerable(
            ScopeCategory.OUT_OF_SCOPE, rationale="nothing in the slice"
        )
        assert p.is_answerable is False
        assert p.confidence == 0.0
        assert p.candidate_answer is None

    def test_confidence_and_risk_are_bounded(self):
        with pytest.raises(ValueError):
            ClassifierProposal(scope=ScopeCategory.IN_SCOPE, confidence=1.5)
        with pytest.raises(ValueError):
            ClassifierProposal(scope=ScopeCategory.IN_SCOPE, risk=-0.1)

    def test_is_frozen(self):
        p = ClassifierProposal.not_answerable(ScopeCategory.OUT_OF_SCOPE)
        with pytest.raises(Exception):
            p.confidence = 0.9  # type: ignore[misc]


class TestVerificationResult:
    def test_affirm(self):
        v = VerificationResult.affirm(confidence=0.8, notes="all grounded")
        assert v.supported is True
        assert v.unsupported_claims == ()

    def test_veto_names_unsupported_claims(self):
        v = VerificationResult.veto(unsupported_claims=["dose not in slice"])
        assert v.supported is False
        assert v.confidence == 0.0
        assert "dose not in slice" in v.unsupported_claims

    def test_is_frozen(self):
        v = VerificationResult.affirm(confidence=0.5)
        with pytest.raises(Exception):
            v.supported = False  # type: ignore[misc]
