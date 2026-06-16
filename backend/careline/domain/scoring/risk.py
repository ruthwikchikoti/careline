"""Risk scoring — fact-kind weighting + scope/flag adjustments (VI-2).

The risk score reflects how dangerous a wrong answer would be for this
particular question.  It is consumed by the RiskGate: a high-risk topic
escalates even at high confidence, because the cost of being wrong is too high.

Risk is derived from:

1. **Fact-kind weights** — medication/allergy answers carry the most risk;
   follow-up logistics carry the least.
2. **Scope signal** — cross-condition or red-flag questions carry maximum risk.
3. **Proposal risk** — the Reasoner's self-assessed risk for this question.

The output is a ``float`` in ``[0.0, 1.0]`` where 1.0 = maximum clinical risk
(e.g. medication interaction, red-flag emergency).

Owner: Vinay (scope ``safety``).
"""

from __future__ import annotations

from careline.domain.enums import FactKind, ScopeCategory
from careline.domain.model.patient import ValidSlice
from careline.domain.model.proposal import ClassifierProposal

# ---------------------------------------------------------------------------
# Risk weight by fact kind
# ---------------------------------------------------------------------------
# Medication answers carry the most risk because a wrong dose/interaction
# answer is a direct patient safety incident.

_KIND_RISK: dict[FactKind, float] = {
    FactKind.MEDICATION: 0.9,
    FactKind.ALLERGY: 0.85,
    FactKind.DIAGNOSIS: 0.7,
    FactKind.INSTRUCTION: 0.5,
    FactKind.OBSERVATION: 0.3,
    FactKind.FOLLOW_UP: 0.2,
}


def compute_risk(
    proposal: ClassifierProposal,
    valid_slice: ValidSlice,
) -> float:
    """Compute the risk score for a turn.

    Returns a ``float`` in ``[0.0, 1.0]`` where 1.0 = maximum clinical risk.
    """
    # -- Scope-based overrides (maximum risk for dangerous scopes) ------------

    # Red-flag: maximum risk, no further analysis needed
    if proposal.scope is ScopeCategory.RED_FLAG:
        return 1.0

    # Cross-condition: near-maximum (can't safely merge guidance)
    if proposal.scope is ScopeCategory.CROSS_CONDITION:
        return 0.95

    # Administrative questions carry minimal risk (logistics, not clinical)
    if proposal.scope is ScopeCategory.ADMINISTRATIVE:
        return 0.1

    # Out-of-scope: high risk (we don't know what we don't know)
    if proposal.scope is ScopeCategory.OUT_OF_SCOPE:
        return 0.8

    # -- In-scope: derive risk from the kinds of facts cited ------------------

    if not proposal.citations or valid_slice.is_empty:
        # No facts cited → risk comes from the proposal's self-assessment
        return max(0.0, min(1.0, proposal.risk))

    # Map cited fact IDs to their fact objects
    cited_ids = set(proposal.citations)
    cited_facts = [f for f in valid_slice.facts if f.id in cited_ids]

    if not cited_facts:
        # Citations don't match any facts in slice — use proposal risk
        return max(0.0, min(1.0, proposal.risk))

    # Risk is the maximum kind-risk among cited facts
    kind_risk = max(_KIND_RISK.get(f.kind, 0.5) for f in cited_facts)

    # Blend: 70% kind-derived, 30% reasoner's self-assessment
    blended = 0.7 * kind_risk + 0.3 * max(0.0, min(1.0, proposal.risk))

    return round(min(1.0, max(0.0, blended)), 4)


__all__ = ["compute_risk"]
