"""Keyless offline twins of the Reasoner and Verifier (SR-2).

These are deterministic, dependency-free stand-ins for the live LLM agents so the
**entire suite runs with no API key**. They are the default the factory wires in
(SR-6), which is what lets M0 — and every CI run — be fully offline.

They are intentionally *simple and conservative*, never "clever":

* :class:`HeuristicReasoner` grounds strictly in the valid slice. It proposes a
  candidate **only** when the question overlaps an approved, currently-valid fact,
  and the candidate is the doctor-approved ``summary`` surfaced verbatim — never
  invented phrasing. No match → it declines (``not_answerable``), biasing the turn
  toward ESCALATE. It flags ``CROSS_CONDITION`` when a question straddles two
  distinct diagnoses (which must not be merged) and ``ADMINISTRATIVE`` for logistics.

* :class:`HeuristicVerifier` is an *independent* second pass: it re-checks that every
  citation is in the valid slice and that every content word of the candidate is
  grounded in a cited fact. Any ungrounded claim, or a citation not in the slice,
  is a veto. It assumes nothing about how the candidate was built.

Because the twin Reasoner surfaces summaries verbatim, the twin Verifier affirms
its own pair cleanly — but the Verifier's checks are written to catch a *real* LLM
that paraphrases beyond the facts, which is exactly the adversarial role it plays
in production.

Owner: Srujan (scope ``llm``).
"""

from __future__ import annotations

from careline.adapters.llm._text import CONNECTIVES, content_words, overlap
from careline.domain.enums import FactKind, ScopeCategory
from careline.domain.model.fact import Fact
from careline.domain.model.patient import ValidSlice
from careline.domain.model.proposal import ClassifierProposal, VerificationResult
from careline.domain.ports.reasoning import Reasoner, Verifier

# A question must cover at least this fraction of its content words with one fact
# before the heuristic will treat that fact as answering it. Tuned so a focused
# question ("paracetamol dose") matches its fact while an unrelated one does not.
_MATCH_THRESHOLD = 0.5

# Logistics terms that classify a question as ADMINISTRATIVE rather than clinical.
_ADMIN_TERMS: frozenset[str] = frozenset(
    {
        "appointment", "appointments", "reschedule", "schedule", "booking", "book",
        "timing", "timings", "billing", "bill", "cost", "price", "payment", "pay",
        "insurance", "receipt", "invoice", "refund", "cancel",
    }
)

# Self-assessed clinical risk by fact kind. This is only the Reasoner's *hint*;
# Vinay's risk gate (VI-2) is the authority. Higher = more dangerous to get wrong.
_RISK_BY_KIND: dict[FactKind, float] = {
    FactKind.ALLERGY: 0.6,
    FactKind.MEDICATION: 0.5,
    FactKind.DIAGNOSIS: 0.45,
    FactKind.INSTRUCTION: 0.35,
    FactKind.OBSERVATION: 0.25,
    FactKind.FOLLOW_UP: 0.15,
}


def _fact_terms(fact: Fact) -> frozenset[str]:
    """All content words a fact exposes — its summary plus its structured strings.

    Pulling in the subtype's string fields (drug name, dose, condition, …) means a
    question can match on the structured detail, not only the prose summary.
    """
    parts = [fact.summary]
    skip = {"id", "kind", "validity", "summary", "approved_by", "approved_at"}
    for key, value in fact.model_dump().items():
        if key in skip:
            continue
        if isinstance(value, str):
            parts.append(value)
    return content_words(" ".join(parts))


def _distinct_conditions(facts: list[Fact]) -> set[str]:
    """The distinct diagnosed conditions among ``facts`` (for cross-condition flagging)."""
    return {
        getattr(f, "condition")
        for f in facts
        if f.kind is FactKind.DIAGNOSIS and getattr(f, "condition", None)
    }


class HeuristicReasoner(Reasoner):
    """Offline Reasoner: grounds strictly in the valid slice by token overlap."""

    def propose(self, *, question: str, context: ValidSlice) -> ClassifierProposal:
        q_terms = content_words(question)
        is_admin = bool(q_terms & _ADMIN_TERMS)

        if not q_terms:
            return ClassifierProposal.not_answerable(
                ScopeCategory.OUT_OF_SCOPE,
                rationale="no parseable clinical content in the question",
            )

        if context.is_empty:
            # Nothing approved and currently valid → the doctor never established an
            # answer. Decline; the spine escalates.
            return ClassifierProposal.not_answerable(
                ScopeCategory.ADMINISTRATIVE if is_admin else ScopeCategory.OUT_OF_SCOPE,
                rationale="no approved, currently-valid facts to answer from",
            )

        scored = sorted(
            ((overlap(q_terms, _fact_terms(f)), f) for f in context.facts),
            key=lambda pair: pair[0],
            reverse=True,
        )

        # Cross-condition is checked *first*, on any touched diagnosis (overlap > 0),
        # because a question that straddles two conditions won't strongly match
        # either one — yet merging them is precisely the leak we must refuse.
        touched = [f for score, f in scored if score > 0.0]
        if len(_distinct_conditions(touched)) > 1:
            return ClassifierProposal.not_answerable(
                ScopeCategory.CROSS_CONDITION,
                rationale="question straddles two distinct diagnosed conditions",
            )

        best_score = scored[0][0]
        if best_score < _MATCH_THRESHOLD:
            return ClassifierProposal.not_answerable(
                ScopeCategory.ADMINISTRATIVE if is_admin else ScopeCategory.OUT_OF_SCOPE,
                rationale="question does not match any approved, currently-valid fact",
            )

        matched = [f for score, f in scored if score >= _MATCH_THRESHOLD]

        candidate = " ".join(f.summary for f in matched)
        citations = tuple(f.id for f in matched)
        confidence = min(0.95, 0.5 + 0.5 * best_score)
        risk = max(_RISK_BY_KIND.get(f.kind, 0.3) for f in matched)
        return ClassifierProposal.answerable(
            candidate,
            citations=citations,
            confidence=round(confidence, 4),
            risk=risk,
            scope=ScopeCategory.ADMINISTRATIVE if is_admin else ScopeCategory.IN_SCOPE,
            rationale=f"grounded in {len(matched)} valid fact(s) by token overlap",
        )


class HeuristicVerifier(Verifier):
    """Offline Verifier: independently re-checks that the candidate is grounded."""

    def verify(
        self,
        *,
        question: str,
        proposal: ClassifierProposal,
        context: ValidSlice,
    ) -> VerificationResult:
        if not proposal.is_answerable:
            return VerificationResult.veto(notes="no grounded candidate to verify")

        valid_ids = set(context.citations)
        stray = [c for c in proposal.citations if c not in valid_ids]
        if stray:
            # A cited fact that isn't in the current valid slice is the worst case:
            # a superseded/unapproved fact. Veto hard.
            return VerificationResult.veto(
                unsupported_claims=tuple(f"citation {c} not in the valid slice" for c in stray),
                notes="candidate cites a fact that is not approved-and-valid now",
            )

        cited = [f for f in context.facts if f.id in set(proposal.citations)]
        grounded_terms: frozenset[str] = frozenset().union(*(_fact_terms(f) for f in cited)) if cited else frozenset()
        candidate_terms = content_words(proposal.candidate_answer)
        ungrounded = candidate_terms - grounded_terms - CONNECTIVES
        if ungrounded:
            return VerificationResult.veto(
                unsupported_claims=tuple(sorted(ungrounded)),
                notes="candidate contains claims not grounded in any cited valid fact",
            )

        covered = len(candidate_terms & grounded_terms) / len(candidate_terms) if candidate_terms else 0.0
        confidence = round(min(0.95, 0.5 + 0.45 * covered), 4)
        return VerificationResult.affirm(
            confidence=confidence,
            notes=f"every candidate claim grounded in {len(cited)} cited valid fact(s)",
        )


__all__ = ["HeuristicReasoner", "HeuristicVerifier"]
