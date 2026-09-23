"""SR-5 — Anthropic adapters: structured mapping + the fail-closed contract.

The suite stays keyless: a fake client is injected, so the real SDK is never
imported or called. These tests pin the safety contract, not the wire format.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from careline.adapters.llm.anthropic_backend import AnthropicReasoner, AnthropicVerifier
from careline.adapters.llm.schemas import ProposalDTO, VerificationDTO
from careline.domain.enums import ScopeCategory
from careline.domain.model.patient import ValidSlice
from careline.domain.model.proposal import ClassifierProposal
from careline.domain.ports.reasoning import ReasonerUnavailable

NOW = datetime(2026, 6, 19, tzinfo=timezone.utc)
CTX = ValidSlice(as_of=NOW, facts=())


class _Response:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class _FakeMessages:
    def __init__(self, *, returns=None, raises=None):
        self._returns = returns
        self._raises = raises
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        if self._raises is not None:
            raise self._raises
        return _Response(self._returns)


class _FakeClient:
    def __init__(self, *, returns=None, raises=None):
        self.messages = _FakeMessages(returns=returns, raises=raises)


class TestAnthropicReasoner:
    def test_maps_structured_proposal_to_domain(self):
        dto = ProposalDTO(
            scope=ScopeCategory.IN_SCOPE,
            candidate_answer="Take 500mg paracetamol.",
            citations=["med-1"],
            confidence=0.9,
        )
        reasoner = AnthropicReasoner(client=_FakeClient(returns=dto))
        proposal = reasoner.propose(question="dose?", context=CTX)
        assert proposal.is_answerable
        assert proposal.citations == ("med-1",)

    def test_never_sends_sampling_knobs_and_uses_adaptive_thinking(self):
        client = _FakeClient(returns=ProposalDTO(scope=ScopeCategory.OUT_OF_SCOPE))
        AnthropicReasoner(client=client).propose(question="q", context=CTX)
        kwargs = client.messages.last_kwargs
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["output_config"] == {"effort": "high"}
        for forbidden in ("temperature", "top_p", "top_k", "budget_tokens"):
            assert forbidden not in kwargs

    def test_sdk_error_fails_closed(self):
        reasoner = AnthropicReasoner(client=_FakeClient(raises=RuntimeError("boom")))
        with pytest.raises(ReasonerUnavailable):
            reasoner.propose(question="q", context=CTX)

    def test_none_parse_fails_closed(self):
        # A refusal or schema-mismatch surfaces as parsed_output=None → escalate.
        reasoner = AnthropicReasoner(client=_FakeClient(returns=None))
        with pytest.raises(ReasonerUnavailable):
            reasoner.propose(question="q", context=CTX)


class TestAnthropicVerifier:
    def test_maps_verification_to_domain(self):
        dto = VerificationDTO(supported=True, confidence=0.8)
        verifier = AnthropicVerifier(client=_FakeClient(returns=dto))
        proposal = ClassifierProposal.answerable("x", citations=["f"], confidence=0.9)
        result = verifier.verify(question="q", proposal=proposal, context=CTX)
        assert result.supported is True

    def test_sdk_error_fails_closed(self):
        verifier = AnthropicVerifier(client=_FakeClient(raises=RuntimeError("boom")))
        proposal = ClassifierProposal.answerable("x", citations=["f"], confidence=0.9)
        with pytest.raises(ReasonerUnavailable):
            verifier.verify(question="q", proposal=proposal, context=CTX)
