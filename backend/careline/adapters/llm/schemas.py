"""LLM structured-output DTOs and their domain mappers (SR-3).

These are the **wire contract** the live LLM agents are constrained to emit — the
exact JSON shape passed to Anthropic's ``messages.parse`` and OpenAI's
``text_format``. They are deliberately a *separate* layer from the domain handoffs
(:class:`ClassifierProposal` / :class:`VerificationResult`): the model fills a flat,
strict DTO, then a mapper lifts it into the validated domain object. That boundary
is what lets us swap providers without touching domain code.

Two invariants make these safe as an LLM contract:

* ``extra="forbid"`` → the emitted JSON Schema carries ``additionalProperties:
  false``, so the model cannot smuggle in unmodelled fields.
* The vocabularies are the **domain enums themselves** (``ScopeCategory``), not
  hand-copied strings — so the LLM's allowed scopes can never drift from the gate
  chain's.

The mappers are intentionally non-raising: raw model output may be malformed (a
candidate with no citations, an out-of-scope candidate), and the *gate chain*, not
the parser, is the safety authority. The mapper produces a faithful, well-typed
domain object; ``ClassifierProposal.is_answerable`` and the gates then fail closed.

Owner: Srujan (scope ``llm``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.enums import ScopeCategory
from careline.domain.model.proposal import ClassifierProposal, VerificationResult


class ProposalDTO(BaseModel):
    """The strict JSON the Reasoner LLM must emit (mirror of a proposal)."""

    model_config = ConfigDict(extra="forbid")

    scope: ScopeCategory = Field(description="Which class the question falls in.")
    candidate_answer: str | None = Field(
        default=None,
        description="Answer drafted ONLY from the supplied facts; null if unanswerable.",
    )
    citations: list[str] = Field(
        default_factory=list,
        description="Ids of the supplied facts the candidate answer relies on.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", description="One-line justification for the trace.")

    def to_domain(self) -> ClassifierProposal:
        """Lift the raw DTO into the validated domain proposal (never raises).

        Faithful, not corrective: a candidate with no citations maps to a proposal
        that is simply not answerable — the safe outcome — rather than an error.
        """
        return ClassifierProposal(
            scope=self.scope,
            candidate_answer=self.candidate_answer,
            citations=tuple(self.citations),
            confidence=self.confidence,
            risk=self.risk,
            rationale=self.rationale,
        )


class VerificationDTO(BaseModel):
    """The strict JSON the Verifier LLM must emit (mirror of a verification)."""

    model_config = ConfigDict(extra="forbid")

    supported: bool = Field(
        description="True ONLY if every claim is grounded in a cited supplied fact."
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    unsupported_claims: list[str] = Field(
        default_factory=list,
        description="Claims that could not be grounded (empty when supported).",
    )
    notes: str = Field(default="", description="One-line justification for the trace.")

    def to_domain(self) -> VerificationResult:
        """Lift the raw DTO into the validated domain verification (never raises)."""
        return VerificationResult(
            supported=self.supported,
            confidence=self.confidence,
            unsupported_claims=tuple(self.unsupported_claims),
            notes=self.notes,
        )


__all__ = ["ProposalDTO", "VerificationDTO"]
