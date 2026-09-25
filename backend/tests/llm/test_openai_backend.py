"""SR-7 — OpenAI adapters: structured mapping, fail-closed, + a skipped live smoke."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from careline.adapters.llm.openai_backend import OpenAIReasoner, OpenAIVerifier
from careline.adapters.llm.schemas import ProposalDTO, VerificationDTO
from careline.domain.enums import ScopeCategory
from careline.domain.model.patient import ValidSlice
from careline.domain.model.proposal import ClassifierProposal
from careline.domain.ports.reasoning import ReasonerUnavailable

NOW = datetime(2026, 6, 22, tzinfo=timezone.utc)
CTX = ValidSlice(as_of=NOW, facts=())


class _Response:
    def __init__(self, output_parsed):
        self.output_parsed = output_parsed


class _FakeResponses:
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
        self.responses = _FakeResponses(returns=returns, raises=raises)


class TestOpenAIReasoner:
    def test_maps_structured_proposal_to_domain(self):
        dto = ProposalDTO(
            scope=ScopeCategory.IN_SCOPE,
            candidate_answer="Take 500mg paracetamol.",
            citations=["med-1"],
            confidence=0.9,
        )
        proposal = OpenAIReasoner(client=_FakeClient(returns=dto)).propose(
            question="dose?", context=CTX
        )
        assert proposal.is_answerable
        assert proposal.citations == ("med-1",)

    def test_uses_text_format_and_no_sampling_knobs(self):
        client = _FakeClient(returns=ProposalDTO(scope=ScopeCategory.OUT_OF_SCOPE))
        OpenAIReasoner(client=client).propose(question="q", context=CTX)
        kwargs = client.responses.last_kwargs
        assert kwargs["text_format"] is ProposalDTO
        for forbidden in ("temperature", "top_p"):
            assert forbidden not in kwargs

    def test_sdk_error_fails_closed(self):
        reasoner = OpenAIReasoner(client=_FakeClient(raises=RuntimeError("boom")))
        with pytest.raises(ReasonerUnavailable):
            reasoner.propose(question="q", context=CTX)

    def test_none_parse_fails_closed(self):
        reasoner = OpenAIReasoner(client=_FakeClient(returns=None))
        with pytest.raises(ReasonerUnavailable):
            reasoner.propose(question="q", context=CTX)


class TestOpenAIVerifier:
    def test_maps_verification_to_domain(self):
        verifier = OpenAIVerifier(client=_FakeClient(returns=VerificationDTO(supported=False)))
        proposal = ClassifierProposal.answerable("x", citations=["f"], confidence=0.9)
        result = verifier.verify(question="q", proposal=proposal, context=CTX)
        assert result.supported is False


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="live smoke: set OPENAI_API_KEY to run against the real Responses API",
)
def test_live_smoke_real_proposal():
    proposal = OpenAIReasoner().propose(question="what is my dose?", context=CTX)
    # Empty slice → the model must decline (fail-closed grounding contract).
    assert not proposal.is_answerable
