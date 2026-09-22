"""SR-3 — structured-output DTOs: strict schema + faithful, fail-closed mapping."""

from __future__ import annotations

import pytest

from careline.adapters.llm.schemas import ProposalDTO, VerificationDTO
from careline.domain.enums import ScopeCategory


class TestProposalDTO:
    def test_schema_forbids_additional_properties(self):
        # The LLM contract must not allow unmodelled fields.
        schema = ProposalDTO.model_json_schema()
        assert schema["additionalProperties"] is False

    def test_rejects_unknown_field(self):
        with pytest.raises(ValueError):
            ProposalDTO(scope="in_scope", confidence=0.5, sneaky="x")  # type: ignore[call-arg]

    def test_maps_answerable_proposal_to_domain(self):
        dto = ProposalDTO(
            scope=ScopeCategory.IN_SCOPE,
            candidate_answer="Take 500mg paracetamol.",
            citations=["med-1"],
            confidence=0.88,
            risk=0.5,
        )
        proposal = dto.to_domain()
        assert proposal.is_answerable
        assert proposal.citations == ("med-1",)
        assert proposal.scope is ScopeCategory.IN_SCOPE

    def test_candidate_without_citations_maps_to_not_answerable(self):
        # Malformed LLM output (answer, no citation) must fail closed, not raise.
        dto = ProposalDTO(
            scope=ScopeCategory.IN_SCOPE,
            candidate_answer="trust me",
            citations=[],
            confidence=0.99,
        )
        proposal = dto.to_domain()
        assert proposal.is_answerable is False

    def test_confidence_bounds_enforced(self):
        with pytest.raises(ValueError):
            ProposalDTO(scope=ScopeCategory.IN_SCOPE, confidence=1.2)


class TestVerificationDTO:
    def test_schema_forbids_additional_properties(self):
        schema = VerificationDTO.model_json_schema()
        assert schema["additionalProperties"] is False

    def test_maps_to_domain(self):
        dto = VerificationDTO(
            supported=False, confidence=0.0, unsupported_claims=["bad claim"]
        )
        result = dto.to_domain()
        assert result.supported is False
        assert result.unsupported_claims == ("bad claim",)
