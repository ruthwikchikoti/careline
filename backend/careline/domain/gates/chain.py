"""The 5-gate chain — deterministic verdict pipeline (VI-3).

This is the Gatekeeper agent's core: five gates run in strict order, each
able to force a terminal verdict (ESCALATE or CLARIFY).  If all five pass,
the turn is promoted to ANSWER.  Each gate records a :class:`TraceStep` so the
verdict is explainable after the fact.

Gate order (defense-in-depth — gates only *downgrade*, never upgrade):

1. **ScopeGate** — red-flag / acute concern / symptom report (re-checked for
   EVERY scope, v4) → ESCALATE; out-of-scope → fail-closed checks, else
   redirect (CLARIFY)
2. **RiskGate** — risk > ceiling even at high confidence → ESCALATE
3. **CrossConditionGate** — question spans ≥2 condition groups → ESCALATE
4. **ConfidenceStalenessGate** — confidence < floor, empty/stale slice, or
   unanswerable proposal → CLARIFY or ESCALATE (respects clarify budget)
5. **IndependentVerificationGate** — verifier vetoed → ESCALATE

The chain only downgrades (ANSWER → CLARIFY → ESCALATE), never upgrades.
This is the structural guarantee of the overriding rule: uncertainty always
resolves toward escalation.

**Final invariants (v4 round 2; v6).** Before a turn is promoted to ANSWER, the
question is scanned by every deterministic net with ALL context guards off
(:func:`mentions_danger_concept`). Any danger concept — live, history-
suppressed, hypothetical, denied, or about a pet — downgrades the ANSWER to a
CLARIFY carrying the emergency line (ESCALATE once the clarify budget is
spent). This holds for every scope, and for the Brain and the graph alike
(both call :func:`run_gate_chain`). v6 adds two more, run in the same place:
a **deterministic citation veto** (every cited id must be EXACTLY an id of the
current valid slice, case-sensitive, no duplicates — else CLARIFY, or
ESCALATE with a danger concept / spent budget), and the **LLM-path backstop**
(:func:`mentions_present_body_report`: a present first-person or care-
recipient body-state report is never ANSWERED even when no lexicon names the
symptom). Every RED_FLAG and cross-condition escalation text carries the
112 emergency line (v6). v7 adds the **answer-text grounding check**
(:mod:`careline.domain.gates.grounding`): every dose / strength / frequency /
number token and every drug name in the answer text must appear in a CITED
fact of the current valid slice — a superseded dose behind a current fact's id
is never ANSWERED (CLARIFY, or ESCALATE with a danger concept / spent budget).

Owner: Priyanshu (scope ``safety``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.enums import ScopeCategory, TraceStatus, Verdict
from careline.domain.gates.grounding import (
    facts_containing,
    medication_names,
    ungrounded_tokens,
)
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.domain.model.fact import Fact
from careline.domain.model.patient import ValidSlice
from careline.domain.model.proposal import ClassifierProposal, VerificationResult
from careline.domain.rails.acute_concern import check_acute_concern
from careline.domain.rails.red_flag import check_multi_condition, check_red_flag
from careline.domain.rails.symptom_report import (
    SYMPTOM_REPORT_RISK,
    check_symptom_report,
    mentions_danger_concept,
    mentions_present_body_report,
)
from careline.domain.scoring.confidence import compute_confidence
from careline.domain.scoring.risk import compute_risk
from careline.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds

#: Appended to every redirect / clarify the chain produces (policy v4): a
#: caller who is told "I can't help with that" or "please rephrase" must
#: never be left without the way out. The acute-concern escalation uses it too.
EMERGENCY_LINE: str = (
    "If this is an emergency, call 112 (India) or your local emergency number now."
)


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
    #: v7: this patient's facts that are NOT current at ``now`` (superseded,
    #: not yet valid, unapproved). Never answer material — used only by the
    #: grounding check to recognise a retired drug and name the fact.
    non_current_facts: tuple[Fact, ...] = ()
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
    """Gate 1: red-flag / acute concern (any scope) → ESCALATE; out-of-scope →
    fail-closed checks, else a redirect that always carries the emergency line."""
    if ctx.proposal.scope is ScopeCategory.RED_FLAG:
        ctx.trace.record(
            "scope_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.1",
            detail=f"red-flag scope: {ctx.proposal.rationale or 'emergency tripwire'}",
        )
        return Decision.escalate(
            f"Red-flag detected: {ctx.proposal.rationale or 'emergency keyword matched'}. "
            f"{EMERGENCY_LINE}",
            scope=ScopeCategory.RED_FLAG,
            risk=1.0,
            trace=ctx.trace,
        )

    # Defense in depth, EVERY scope (v4): the scope label is a classifier
    # output and the caller may have skipped triage. Re-run both deterministic
    # emergency nets on the raw question before any downstream gate can ANSWER
    # or redirect. At v3 these ran only for OUT_OF_SCOPE, so an emergency mixed
    # into an in-scope question ("soft diet avoid spicy; I want to die") passed.
    matched = check_red_flag(ctx.question)
    if matched:
        ctx.trace.record(
            "scope_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.1",
            detail=(
                f"scope labelled {ctx.proposal.scope.value} but rail matched "
                f"{matched!r} on the raw question — escalating"
            ),
        )
        return Decision.escalate(
            f"Red-flag detected: {matched}. {EMERGENCY_LINE}",
            scope=ScopeCategory.RED_FLAG,
            risk=1.0,
            trace=ctx.trace,
        )

    acute = check_acute_concern(ctx.question)
    if acute is not None:
        ctx.trace.record(
            "scope_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.1",
            detail=(
                f"first-person acute concern ({acute}) under scope "
                f"{ctx.proposal.scope.value} — escalating rather than answering/redirecting"
            ),
        )
        return Decision.escalate(
            "Your message may describe an urgent symptom this service "
            "cannot assess from your approved record — transferring to "
            f"your doctor. {EMERGENCY_LINE}",
            scope=ctx.proposal.scope,
            risk=0.9,
            trace=ctx.trace,
        )

    report = check_symptom_report(ctx.question)
    if report is not None:
        ctx.trace.record(
            "scope_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.1",
            detail=(
                f"current symptom report ({report}) under scope "
                f"{ctx.proposal.scope.value} — escalating rather than answering/redirecting"
            ),
        )
        return Decision.escalate(
            "Your message may describe an urgent symptom — transferring to your "
            f"doctor. {EMERGENCY_LINE}",
            scope=ScopeCategory.RED_FLAG,
            risk=SYMPTOM_REPORT_RISK,
            trace=ctx.trace,
        )

    if ctx.proposal.scope is ScopeCategory.OUT_OF_SCOPE:
        # Out-of-scope = not a clinical question this patient's approved care plan
        # covers (general knowledge, off-topic, another condition). Redirecting is
        # the right move — escalating it would flood the doctor's queue with
        # non-clinical noise and train them to ignore it (a real safety hazard).
        #
        # The emergency nets already ran above for every scope; what is left
        # here is the out-of-scope-specific fail-closed checks: a cross-
        # condition span the caller's tripwire missed, and an empty valid slice
        # (gate 4's rule, which a mislabelled scope must not be able to shadow).
        # Gates only downgrade: ESCALATE here preempts a would-be CLARIFY.
        is_cross, groups = check_multi_condition(ctx.question)
        if is_cross:
            ctx.trace.record(
                "scope_gate",
                TraceStatus.TERMINAL,
                spec_section="§5.3",
                detail=(
                    "scope labelled out-of-scope but question spans conditions: "
                    f"{', '.join(groups)} — escalating"
                ),
            )
            return Decision.escalate(
                "Question spans multiple clinical conditions — cannot safely merge guidance. "
            f"{EMERGENCY_LINE}",
                scope=ScopeCategory.CROSS_CONDITION,
                risk=0.95,
                trace=ctx.trace,
            )

        if ctx.valid_slice.is_empty:
            ctx.trace.record(
                "scope_gate",
                TraceStatus.TERMINAL,
                spec_section="§5.5",
                detail=(
                    "scope labelled out-of-scope but valid slice is empty — "
                    "fail closed"
                ),
            )
            return Decision.escalate(
                "No approved, currently-valid facts available for this patient.",
                scope=ScopeCategory.OUT_OF_SCOPE,
                risk=compute_risk(ctx.proposal, ctx.valid_slice),
                trace=ctx.trace,
            )

        ctx.trace.record(
            "scope_gate",
            TraceStatus.TERMINAL,
            spec_section="§5.1",
            detail="out of the doctor's established scope — redirecting, not escalating",
        )
        return Decision.clarify(
            "I can only help with the care your doctor approved for you — your "
            "medicines, diet, and post-visit instructions. For anything else, please "
            f"contact the clinic directly. {EMERGENCY_LINE}",
            scope=ScopeCategory.OUT_OF_SCOPE,
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
            "Question spans multiple clinical conditions — cannot safely merge guidance. "
            f"{EMERGENCY_LINE}",
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
                f"Could you rephrase or provide more detail? {EMERGENCY_LINE}",
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
                f"Could you provide more detail about your question? {EMERGENCY_LINE}",
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
# Final invariants (v6)
# ---------------------------------------------------------------------------


def _danger(question: str) -> str | None:
    """Any danger concept (every net, all context guards off), else a present
    body-state report no lexicon names (the v6 LLM-path backstop)."""
    danger = mentions_danger_concept(question)
    if danger is not None:
        return danger
    body = mentions_present_body_report(question)
    return f"body_state:{body}" if body is not None else None


def _citation_veto(ctx: GateContext, danger: str | None) -> Decision | None:
    """Deterministic citation veto (v6): an ANSWER may cite only ids that are
    EXACTLY ids of the current valid slice (case-sensitive), each at most once.

    At v5 citation validity was only a 0.5 grounding ratio inside the
    confidence score; a confident reasoner citing a superseded fact (or a
    case-mangled id) with an affirming LLM verifier was ANSWERED. The keyless
    HeuristicVerifier vetoed stray ids, the live verifiers did not. This runs
    for every engine because the Brain and the graph both call
    :func:`run_gate_chain`. Never ANSWER: CLARIFY with the emergency line, or
    ESCALATE when a danger concept is present or the clarify budget is spent.
    """
    valid_ids = set(ctx.valid_slice.citations)
    cited = list(ctx.proposal.citations)
    stray = [c for c in cited if c not in valid_ids]
    duplicates = sorted({c for c in cited if cited.count(c) > 1})
    if not stray and not duplicates:
        ctx.trace.record(
            "citation_veto",
            TraceStatus.PASS,
            spec_section="§5.5",
            detail=f"all {len(cited)} citation(s) are exact ids in the valid slice",
        )
        return None
    problem = ", ".join(
        [f"not in the valid slice: {stray!r}"] * bool(stray)
        + [f"duplicated: {duplicates!r}"] * bool(duplicates)
    )
    budget_spent = ctx.call_session is not None and not ctx.call_session.can_clarify()
    if danger is not None or budget_spent:
        why = f"danger concept ({danger})" if danger is not None else "clarify budget exhausted"
        ctx.trace.record(
            "citation_veto",
            TraceStatus.TERMINAL,
            spec_section="§5.5",
            detail=f"citation(s) {problem} and {why} — escalating",
        )
        return Decision.escalate(
            "The answer could not be traced to your doctor's current approved "
            f"record — transferring to your doctor. {EMERGENCY_LINE}",
            scope=ctx.proposal.scope,
            risk=compute_risk(ctx.proposal, ctx.valid_slice),
            trace=ctx.trace,
        )
    ctx.trace.record(
        "citation_veto",
        TraceStatus.TERMINAL,
        spec_section="§5.5",
        detail=f"citation(s) {problem} — never ANSWER; clarifying",
    )
    return Decision.clarify(
        "I couldn't match that answer to your doctor's current approved record, "
        "so I won't give it. Could you rephrase, or ask about a specific "
        f"medicine, diet or care instruction? {EMERGENCY_LINE}",
        confidence=0.0,
        scope=ctx.proposal.scope,
        trace=ctx.trace,
    )


def _grounding_veto(ctx: GateContext, danger: str | None) -> Decision | None:
    """Deterministic answer-text grounding (v7): every quantity / frequency /
    number token and every drug name in the answer text must appear in the
    text of a CITED fact of the current valid slice.

    At v6 a confident reasoner could repeat a SUPERSEDED dose ("Metformin
    1000mg") while citing the current fact's id (500mg); with an affirming
    LLM verifier it was ANSWERED. Never ANSWER an ungrounded token: CLARIFY
    with the emergency line, or ESCALATE when a danger concept is present or
    the clarify budget is spent.
    """
    cited_ids = set(ctx.proposal.citations)
    cited = [f for f in ctx.valid_slice.facts if f.id in cited_ids]
    known = medication_names((*ctx.valid_slice.facts, *ctx.non_current_facts))
    missing = ungrounded_tokens(ctx.proposal.candidate_answer or "", cited, known)
    if not missing:
        ctx.trace.record(
            "answer_grounding",
            TraceStatus.PASS,
            spec_section="§5.5",
            detail=f"every dose/number/drug token grounded in {len(cited)} cited fact(s)",
        )
        return None
    notes = []
    for token in missing:
        retired = facts_containing(token, ctx.non_current_facts, known)
        uncited = facts_containing(
            token, [f for f in ctx.valid_slice.facts if f.id not in cited_ids], known
        )
        where = (
            f"only in non-current fact(s) {', '.join(retired)}" if retired
            else f"only in uncited fact(s) {', '.join(uncited)}" if uncited
            else "in no fact"
        )
        notes.append(f"{token!r} {where}")
    problem = "; ".join(notes)
    budget_spent = ctx.call_session is not None and not ctx.call_session.can_clarify()
    if danger is not None or budget_spent:
        why = f"danger concept ({danger})" if danger is not None else "clarify budget exhausted"
        ctx.trace.record(
            "answer_grounding",
            TraceStatus.TERMINAL,
            spec_section="§5.5",
            detail=f"ungrounded answer token(s): {problem}; and {why} — escalating",
        )
        return Decision.escalate(
            "The answer could not be traced to your doctor's current approved "
            f"record — transferring to your doctor. {EMERGENCY_LINE}",
            scope=ctx.proposal.scope,
            risk=compute_risk(ctx.proposal, ctx.valid_slice),
            trace=ctx.trace,
        )
    ctx.trace.record(
        "answer_grounding",
        TraceStatus.TERMINAL,
        spec_section="§5.5",
        detail=f"ungrounded answer token(s): {problem} — never ANSWER; clarifying",
    )
    return Decision.clarify(
        "I couldn't match that answer to your doctor's current approved record, "
        "so I won't give it. Could you rephrase, or ask about a specific "
        f"medicine, diet or care instruction? {EMERGENCY_LINE}",
        confidence=0.0,
        scope=ctx.proposal.scope,
        trace=ctx.trace,
    )


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

    # -- Final invariants (v4 round 2, v6) -----------------------------------
    # (a) a danger concept never ends in ANSWER; (b) v6: nor does a present
    # body-state report the lexicons do not know (the LLM-path backstop);
    # (c) v6: nor does a citation outside the valid slice; (d) v7: nor does an
    # answer-text dose / number / drug token absent from every cited fact.
    danger = _danger(ctx.question)

    veto = _citation_veto(ctx, danger)
    if veto is not None:
        return veto

    veto = _grounding_veto(ctx, danger)
    if veto is not None:
        return veto

    if danger is not None:
        if ctx.call_session is not None and not ctx.call_session.can_clarify():
            ctx.trace.record(
                "danger_concept_invariant",
                TraceStatus.TERMINAL,
                spec_section="§5.1",
                detail=(
                    f"danger concept ({danger}) in the question and clarify budget "
                    "exhausted — escalating instead of answering"
                ),
            )
            return Decision.escalate(
                f"Question mentions a danger concept ({danger}) — transferring to your "
                f"doctor. {EMERGENCY_LINE}",
                scope=ctx.proposal.scope,
                risk=compute_risk(ctx.proposal, ctx.valid_slice),
                trace=ctx.trace,
            )
        ctx.trace.record(
            "danger_concept_invariant",
            TraceStatus.TERMINAL,
            spec_section="§5.1",
            detail=(
                f"danger concept ({danger}) in the question (suppressed or not) — "
                "never ANSWER; clarifying with the emergency line"
            ),
        )
        return Decision.clarify(
            "Your question mentions a symptom I can't safely assess here, so I "
            "won't answer it directly — your doctor can. Is there something "
            "specific about your approved medicines, diet or care instructions I "
            f"can help with? {EMERGENCY_LINE}",
            confidence=compute_confidence(ctx.proposal, ctx.verification, ctx.valid_slice),
            scope=ctx.proposal.scope,
            trace=ctx.trace,
        )

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


__all__ = ["EMERGENCY_LINE", "GateContext", "run_gate_chain"]
