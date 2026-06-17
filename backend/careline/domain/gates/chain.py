"""The 5-gate chain — deterministic verdict pipeline (VI-3).

This is the Gatekeeper agent's core: five gates run in strict order, each
able to force a terminal verdict (ESCALATE or CLARIFY).  If all five pass,
the turn is promoted to ANSWER.  Each gate records a :class:`TraceStep` so the
verdict is explainable after the fact.

Gate order (defense-in-depth — gates only *downgrade*, never upgrade):

1. **ScopeGate** — out-of-scope / red-flag → ESCALATE
2. **RiskGate** — risk > ceiling even at high confidence → ESCALATE
3. **CrossConditionGate** — question spans ≥2 condition groups → ESCALATE
4. **ConfidenceStalenessGate** — confidence < floor, empty/stale slice, or
   unanswerable proposal → CLARIFY or ESCALATE (respects clarify budget)
5. **IndependentVerificationGate** — verifier vetoed → ESCALATE

The chain only downgrades (ANSWER → CLARIFY → ESCALATE), never upgrades.
This is the structural guarantee of the overriding rule: uncertainty always
resolves toward escalation.

Owner: Vinay (scope ``safety``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.enums import ScopeCategory, TraceStatus, Verdict
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.domain.model.patient import ValidSlice
from careline.domain.model.proposal import ClassifierProposal, VerificationResult
from careline.domain.scoring.confidence import compute_confidence
from careline.domain.scoring.risk import compute_risk
from careline.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds


# ---------------------------------------------------------------------------
# GateContext — everything the chain needs
# ---------------------------------------------------------------------------


class GateContext(BaseModel):
    """Everything the 5-gate chain needs to decide a verdict.

    Assembled by the Brain or QuestionService and threaded through the gates.
    """

    model_config = ConfigDict(extra="forbid")

    question: str
    proposal: ClassifierProposal
    verification: VerificationResult | None = None
    valid_slice: ValidSlice
    thresholds: Thresholds = Field(default_factory=lambda: DEFAULT_THRESHOLDS)
    now: datetime
    call_session: CallSession | None = None
    trace: ReasoningTrace = Field(default_factory=ReasoningTrace)


# ---------------------------------------------------------------------------
# Individual gates
# ---------------------------------------------------------------------------
# Each returns ``Decision | None``.  ``None`` = pass (keep going);
# ``Decision`` = terminal (verdict decided, remaining gates are skipped).

_GateFn = Callable[[GateContext], Decision | None]


def _scope_gate(ctx: GateContext) -> Decision | None:
    """Gate 1: out-of-scope or red-flag → ESCALATE."""
    if ctx.proposal.scope is ScopeCategory.RED_FLAG:
        ctx.trace.record(
            "scope_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.1",
            detail=f"red-flag scope: {ctx.proposal.rationale or 'emergency tripwire'}",
        )
        return Decision.escalate(
            f"Red-flag detected: {ctx.proposal.rationale or 'emergency keyword matched'}",
            scope=ScopeCategory.RED_FLAG,
            risk=1.0,
            trace=ctx.trace,
        )

    if ctx.proposal.scope is ScopeCategory.OUT_OF_SCOPE:
        ctx.trace.record(
            "scope_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.1",
            detail="question is out of the doctor's established scope",
        )
        return Decision.escalate(
            "Question is outside the doctor's established scope for this patient.",
            scope=ScopeCategory.OUT_OF_SCOPE,
            risk=0.8,
            trace=ctx.trace,
        )

    ctx.trace.record("scope_gate", TraceStatus.PASS, spec_section="§5.1")
    return None


def _risk_gate(ctx: GateContext) -> Decision | None:
    """Gate 2: risk above ceiling → ESCALATE (even at high confidence)."""
    risk = compute_risk(ctx.proposal, ctx.valid_slice)

    if risk > ctx.thresholds.risk_ceiling:
        ctx.trace.record(
            "risk_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.4",
            detail=f"risk {risk:.2f} > ceiling {ctx.thresholds.risk_ceiling:.2f}",
        )
        return Decision.escalate(
            f"Risk too high ({risk:.2f}) — escalating to doctor.",
            scope=ctx.proposal.scope,
            risk=risk,
            trace=ctx.trace,
        )

    ctx.trace.record(
        "risk_gate",
        TraceStatus.PASS,
        spec_section="§5.4",
        detail=f"risk {risk:.2f} <= ceiling {ctx.thresholds.risk_ceiling:.2f}",
    )
    return None


def _cross_condition_gate(ctx: GateContext) -> Decision | None:
    """Gate 3: question spans >=2 clinical conditions → ESCALATE."""
    if ctx.proposal.scope is ScopeCategory.CROSS_CONDITION:
        ctx.trace.record(
            "cross_condition_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.3",
            detail="question spans multiple clinical conditions",
        )
        return Decision.escalate(
            "Question spans multiple clinical conditions — cannot safely merge guidance.",
            scope=ScopeCategory.CROSS_CONDITION,
            risk=0.95,
            trace=ctx.trace,
        )

    ctx.trace.record("cross_condition_gate", TraceStatus.PASS, spec_section="§5.3")
    return None


def _confidence_staleness_gate(ctx: GateContext) -> Decision | None:
    """Gate 4: low confidence, stale/empty slice → CLARIFY or ESCALATE."""
    confidence = compute_confidence(ctx.proposal, ctx.verification, ctx.valid_slice)

    # Empty valid slice → escalate (nothing to answer from)
    if ctx.valid_slice.is_empty:
        ctx.trace.record(
            "confidence_staleness_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.5",
            detail="valid slice is empty — no approved facts to ground on",
        )
        return Decision.escalate(
            "No approved, currently-valid facts available for this patient.",
            scope=ctx.proposal.scope,
            risk=compute_risk(ctx.proposal, ctx.valid_slice),
            trace=ctx.trace,
        )

    # Not answerable → clarify or escalate
    if not ctx.proposal.is_answerable:
        if ctx.call_session is not None and ctx.call_session.can_clarify():
            ctx.trace.record(
                "confidence_staleness_gate",
                TraceStatus.TERMINAL,
                spec_section="§5.5",
                detail="proposal not answerable — asking for clarification",
            )
            return Decision.clarify(
                "I wasn't able to find a clear answer. "
                "Could you rephrase or provide more detail?",
                confidence=confidence,
                scope=ctx.proposal.scope,
                trace=ctx.trace,
            )
        ctx.trace.record(
            "confidence_staleness_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.5",
            detail="proposal not answerable and clarify budget exhausted — escalating",
        )
        return Decision.escalate(
            "Unable to find a supported answer — transferring to your doctor.",
            scope=ctx.proposal.scope,
            risk=compute_risk(ctx.proposal, ctx.valid_slice),
            trace=ctx.trace,
        )

    # Below confidence floor → clarify or escalate
    if confidence < ctx.thresholds.confidence_floor:
        if ctx.call_session is not None and ctx.call_session.can_clarify():
            ctx.trace.record(
                "confidence_staleness_gate",
                TraceStatus.TERMINAL,
                spec_section="§5.5",
                detail=(
                    f"confidence {confidence:.2f} < floor "
                    f"{ctx.thresholds.confidence_floor:.2f} — clarify"
                ),
            )
            return Decision.clarify(
                "I'm not fully confident in my answer. "
                "Could you provide more detail about your question?",
                confidence=confidence,
                scope=ctx.proposal.scope,
                trace=ctx.trace,
            )
        ctx.trace.record(
            "confidence_staleness_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.5",
            detail=(
                f"confidence {confidence:.2f} < floor and "
                "clarify budget exhausted — escalating"
            ),
        )
        return Decision.escalate(
            "Confidence too low to answer safely — transferring to your doctor.",
            scope=ctx.proposal.scope,
            risk=compute_risk(ctx.proposal, ctx.valid_slice),
            trace=ctx.trace,
        )

    ctx.trace.record(
        "confidence_staleness_gate",
        TraceStatus.PASS,
        spec_section="§5.5",
        detail=(
            f"confidence {confidence:.2f} >= floor "
            f"{ctx.thresholds.confidence_floor:.2f}"
        ),
    )
    return None


def _independent_verification_gate(ctx: GateContext) -> Decision | None:
    """Gate 5: verifier vetoed the candidate → ESCALATE."""
    if ctx.verification is None:
        # No verification available — fail closed
        ctx.trace.record(
            "independent_verification_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.2",
            detail="no verification result available — fail closed",
        )
        return Decision.escalate(
            "Independent verification unavailable — escalating for safety.",
            scope=ctx.proposal.scope,
            risk=compute_risk(ctx.proposal, ctx.valid_slice),
            trace=ctx.trace,
        )

    if not ctx.verification.supported:
        claims = ", ".join(ctx.verification.unsupported_claims) or "unspecified"
        ctx.trace.record(
            "independent_verification_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.2",
            detail=f"verifier vetoed: unsupported claims [{claims}]",
        )
        return Decision.escalate(
            f"Answer not fully supported by patient record: {claims}",
            scope=ctx.proposal.scope,
            risk=compute_risk(ctx.proposal, ctx.valid_slice),
            trace=ctx.trace,
        )

    ctx.trace.record(
        "independent_verification_gate",
        TraceStatus.PASS,
        spec_section="§5.2",
        detail=f"verifier affirmed (confidence {ctx.verification.confidence:.2f})",
    )
    return None


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------

_GATES: tuple[_GateFn, ...] = (
    _scope_gate,
    _risk_gate,
    _cross_condition_gate,
    _confidence_staleness_gate,
    _independent_verification_gate,
)


def run_gate_chain(ctx: GateContext) -> Decision:
    """Run the 5-gate chain and return the final verdict.

    Each gate either terminates (ESCALATE/CLARIFY) or passes.  Remaining gates
    after a terminal are recorded as SKIPPED in the trace.  If all pass, the
    turn is promoted to ANSWER.
    """
    terminal: Decision | None = None

    for gate_fn in _GATES:
        if terminal is not None:
            # A previous gate already decided — skip remaining gates
            ctx.trace.record(gate_fn.__name__.lstrip("_"), TraceStatus.SKIPPED)
            continue
        result = gate_fn(ctx)
        if result is not None:
            terminal = result

    if terminal is not None:
        return terminal

    # -- All gates passed → ANSWER --------------------------------------------
    confidence = compute_confidence(ctx.proposal, ctx.verification, ctx.valid_slice)
    risk = compute_risk(ctx.proposal, ctx.valid_slice)

    ctx.trace.record(
        "final_verdict",
        TraceStatus.PASS,
        detail=(
            f"all gates passed — ANSWER "
            f"(confidence={confidence:.2f}, risk={risk:.2f})"
        ),
    )

    return Decision.answer(
        ctx.proposal.candidate_answer,  # type: ignore[arg-type]
        confidence=confidence,
        risk=risk,
        scope=ctx.proposal.scope,
        citations=list(ctx.proposal.citations),
        trace=ctx.trace,
    )


__all__ = ["GateContext", "run_gate_chain"]
