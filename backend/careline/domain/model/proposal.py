"""The Reasoner/Verifier handoff objects (SR-1).

These are the two structured Pydantic handoffs the reasoning agents speak in —
never free text. The :class:`ClassifierProposal` is what the **Reasoner** emits for
one turn (a scope classification + a *candidate* answer grounded in the valid
slice); the :class:`VerificationResult` is what the **independent Verifier** emits
when it re-checks that candidate against the same slice.

Neither object is a final answer. They are *inputs* the gate chain scores: the
Gatekeeper only promotes a proposal to an ANSWER when the proposal is in-scope,
confident, low-risk **and** the Verifier independently affirms it is supported.
A proposal the Verifier cannot ground is vetoed, and the turn fails closed to
ESCALATE — exactly the overriding rule.

Owner: Srujan (scope ``llm``). The shapes are a frozen interface: the Reasoner /
Verifier ports return them, the offline twins and the live LLM adapters build them,
and Vinay's gate chain consumes them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.enums import ScopeCategory


class ClassifierProposal(BaseModel):
    """The Reasoner's structured proposal for one turn — *not* the final answer.

    Carries the scope the question falls in, a candidate answer drafted **only**
    from the valid slice (``None`` when nothing answerable was found), the ids of
    the facts that candidate leans on, and the Reasoner's self-assessed confidence
    and risk. Build via the named factories so the answerable/non-answerable
    invariants hold.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: ScopeCategory
    candidate_answer: str | None = Field(
        default=None,
        description="Draft answer grounded in the valid slice; None if not answerable.",
    )
    citations: tuple[str, ...] = Field(
        default=(),
        description="Ids of the valid facts the candidate answer leans on.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(
        default="",
        description="Why the Reasoner classified/answered as it did (for the trace).",
    )

    @property
    def is_answerable(self) -> bool:
        """True when the Reasoner produced a non-empty candidate grounded in facts.

        Fail-closed: a candidate with no citations is *not* answerable — an answer
        must always trace to at least one valid fact, never to the model's prior.
        """
        return bool(self.candidate_answer and self.candidate_answer.strip()) and bool(
            self.citations
        )

    @classmethod
    def answerable(
        cls,
        candidate_answer: str,
        *,
        citations: tuple[str, ...] | list[str],
        confidence: float,
        risk: float = 0.0,
        scope: ScopeCategory = ScopeCategory.IN_SCOPE,
        rationale: str = "",
    ) -> "ClassifierProposal":
        """A proposal that offers a grounded candidate answer."""
        if not candidate_answer or not candidate_answer.strip():
            raise ValueError("an answerable proposal must carry a non-empty candidate_answer")
        if not citations:
            raise ValueError("an answerable proposal must cite at least one valid fact")
        return cls(
            scope=scope,
            candidate_answer=candidate_answer,
            citations=tuple(citations),
            confidence=confidence,
            risk=risk,
            rationale=rationale,
        )

    @classmethod
    def not_answerable(
        cls,
        scope: ScopeCategory,
        *,
        rationale: str = "",
        risk: float = 0.0,
    ) -> "ClassifierProposal":
        """A proposal that declines to answer — the slice did not support one.

        Confidence is pinned to 0.0: declining to answer is never a confident answer.
        """
        return cls(
            scope=scope,
            candidate_answer=None,
            citations=(),
            confidence=0.0,
            risk=risk,
            rationale=rationale,
        )


class VerificationResult(BaseModel):
    """The independent Verifier's affirm/veto on a :class:`ClassifierProposal`.

    The Verifier re-derives, from the same valid slice, whether *every* claim in the
    candidate answer is supported by a cited fact. ``supported`` is the veto bit the
    Gatekeeper reads; ``unsupported_claims`` names what failed so the trace explains
    the veto.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    supported: bool = Field(
        ..., description="True only if every claim is grounded in a cited valid fact."
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    unsupported_claims: tuple[str, ...] = Field(
        default=(),
        description="Claims in the candidate the Verifier could not ground (the veto reasons).",
    )
    notes: str = Field(default="", description="Verifier rationale for the trace.")

    @classmethod
    def affirm(cls, *, confidence: float, notes: str = "") -> "VerificationResult":
        """The candidate is fully grounded — let the gate chain consider it."""
        return cls(supported=True, confidence=confidence, unsupported_claims=(), notes=notes)

    @classmethod
    def veto(
        cls,
        *,
        unsupported_claims: tuple[str, ...] | list[str] = (),
        notes: str = "",
        confidence: float = 0.0,
    ) -> "VerificationResult":
        """The candidate is not fully grounded — the turn must fail closed."""
        return cls(
            supported=False,
            confidence=confidence,
            unsupported_claims=tuple(unsupported_claims),
            notes=notes,
        )


__all__ = ["ClassifierProposal", "VerificationResult"]
