"""Confidence scoring — weighted geometric mean with hard-zeros (VI-2).

The confidence score is the single number the ConfidenceStaleness gate reads
to decide whether the agent is confident enough to answer.  It combines three
signals into a weighted geometric mean:

1. **Reasoner confidence** — the LLM's self-assessed confidence.
2. **Verifier confidence** — the independent verifier's confidence.
3. **Grounding ratio** — ``cited facts / total valid facts``.

**Hard-zeros** enforce the safety invariant: if *any* of the following are true,
the confidence is forced to **exactly 0.0** regardless of how confident the
LLM claims to be:

- The question is out-of-scope or a red flag.
- The valid slice is empty (no facts to ground on).
- The proposal is not answerable (no candidate / no citations).
- The verifier vetoed the candidate.

This means a stale, unsupported, or out-of-scope answer can **never** clear
the confidence threshold — the gate chain will always route it to CLARIFY or
ESCALATE.

Owner: Vinay (scope ``safety``).
"""

from __future__ import annotations

import math

from careline.domain.enums import ScopeCategory
from careline.domain.model.patient import ValidSlice
from careline.domain.model.proposal import ClassifierProposal, VerificationResult

# ---------------------------------------------------------------------------
# Weights for the geometric mean
# ---------------------------------------------------------------------------

_W_REASONER: float = 0.4
_W_VERIFIER: float = 0.3
_W_GROUNDING: float = 0.3


def _weighted_geometric_mean(
    values: list[tuple[float, float]],
) -> float:
    """Weighted geometric mean of ``(value, weight)`` pairs.

    All values must be in ``[0, 1]``.  If any value is 0 the result is 0 —
    this is the geometric mean's natural hard-zero property, and it is the
    mechanism behind our safety hard-zeros.
    """
    if not values:
        return 0.0
    log_sum = 0.0
    weight_sum = 0.0
    for v, w in values:
        if v <= 0.0:
            return 0.0  # hard zero
        log_sum += w * math.log(v)
        weight_sum += w
    if weight_sum <= 0.0:
        return 0.0
    return math.exp(log_sum / weight_sum)


def compute_confidence(
    proposal: ClassifierProposal,
    verification: VerificationResult | None,
    valid_slice: ValidSlice,
) -> float:
    """Compute the final confidence score for a turn.

    Returns a ``float`` in ``[0.0, 1.0]``.  Hard-zeros on any safety-critical
    condition so that no stale or unsupported answer ever clears the threshold.
    """
    # -- Hard-zero conditions (any one → confidence = 0.0) --------------------

    # Out-of-scope or red-flag: we have no business answering
    if proposal.scope in (ScopeCategory.OUT_OF_SCOPE, ScopeCategory.RED_FLAG):
        return 0.0

    # Empty valid slice: nothing to ground on
    if valid_slice.is_empty:
        return 0.0

    # Not answerable: no candidate or no citations
    if not proposal.is_answerable:
        return 0.0

    # Verifier vetoed: the independent check failed
    if verification is not None and not verification.supported:
        return 0.0

    # -- Component scores -----------------------------------------------------

    reasoner_conf = max(0.0, min(1.0, proposal.confidence))

    verifier_conf = (
        max(0.0, min(1.0, verification.confidence))
        if verification is not None
        else 0.5  # no verifier = neutral (should not happen in real flow)
    )

    # Grounding ratio: are the cited facts real (present in the valid slice)?
    # This measures citation *validity*, not coverage — a question about one
    # medication legitimately cites one fact, so dividing by the patient's total
    # fact count would wrongly punish a richer record (and a fabricated/superseded
    # citation, which is the real hazard, still drags this down and the verifier
    # hard-zeros it).
    valid_ids = {fact.id for fact in valid_slice.facts}
    citations = proposal.citations
    grounding = (
        sum(1 for cid in citations if cid in valid_ids) / len(citations)
        if citations
        else 0.0
    )

    # -- Weighted geometric mean ----------------------------------------------

    score = _weighted_geometric_mean([
        (reasoner_conf, _W_REASONER),
        (verifier_conf, _W_VERIFIER),
        (grounding, _W_GROUNDING),
    ])

    return round(min(1.0, max(0.0, score)), 4)


__all__ = ["compute_confidence"]
