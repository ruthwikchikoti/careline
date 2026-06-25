"""The Brain — CareLine's single headless safety authority (RU-3).

One question in, one terminal :class:`Decision` out. The Brain runs the safety
spine end to end:

    red-flag rail → multi-condition tripwire → valid slice → reasoner
    → (lazy) verifier → 5-gate chain → Decision + reasoning trace

It is deliberately **headless**: no telephony, no audit, no session mutation, no
tracing spans. Those are application concerns that wrap the Brain (Vinay's
``QuestionService``) or observe it (the LangGraph nodes). The Brain only *decides*.

Why a separate object from ``QuestionService`` and the graph: this is the one place
the verdict is computed. The multi-node LangGraph (RU-4) delegates to it node by
node and a **parity test** (RU-5) asserts the graph and the Brain never disagree —
so the multi-agent presentation can never drift from the verified decision core.

Design invariants:
- **Fail closed.** A reasoner/verifier that raises :class:`ReasonerUnavailable`
  resolves to ESCALATE, never a guess.
- **Lazy verifier.** The Verifier only runs when the Reasoner actually produced an
  answerable candidate — there is nothing to independently check otherwise.
- **No mutation of inputs.** ``session`` is read (``can_clarify``) but never
  advanced here; the caller owns session/turn accounting.

Owner: Ruthwik (scope ``brain``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from careline.domain.enums import ScopeCategory, TraceStatus
from careline.domain.gates.chain import GateContext, run_gate_chain
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.domain.model.patient import Patient
from careline.domain.ports.reasoning import Reasoner, ReasonerUnavailable, Verifier
from careline.domain.rails.conversational import is_small_talk
from careline.domain.rails.red_flag import check_multi_condition, check_red_flag
from careline.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds


class Brain:
    """The headless decision pipeline. Inject the reasoning ports; call per turn."""

    def __init__(
        self,
        *,
        reasoner: Reasoner,
        verifier: Verifier,
        thresholds: Thresholds | None = None,
    ) -> None:
        self._reasoner = reasoner
        self._verifier = verifier
        self._thresholds = thresholds or DEFAULT_THRESHOLDS

    @property
    def thresholds(self) -> Thresholds:
        return self._thresholds

    def run_question(
        self,
        *,
        question: str,
        patient: Patient,
        now: datetime | None = None,
        session: CallSession | None = None,
        trace: ReasoningTrace | None = None,
    ) -> Decision:
        """Decide one question for one patient and return the terminal ``Decision``.

        ``trace`` may be supplied so a caller (e.g. a graph node) threads its own
        trace through; otherwise a fresh one is started. A single ``now`` drives
        every temporal check for this turn.
        """
        now = now or datetime.now(timezone.utc)
        trace = trace if trace is not None else ReasoningTrace()

        # -- Pre-LLM triage: red-flag rail (bypasses the LLM entirely) ---------
        matched = check_red_flag(question)
        if matched:
            trace.record(
                "red_flag_rail",
                TraceStatus.TERMINAL,
                spec_section="§5.1",
                detail=f"emergency keyword matched: {matched!r}",
            )
            return Decision.escalate(
                f"Emergency symptom detected ({matched}) — transferring to your doctor.",
                scope=ScopeCategory.RED_FLAG,
                risk=1.0,
                trace=trace,
            )

        # -- Pre-LLM triage: multi-condition tripwire -------------------------
        is_cross, groups = check_multi_condition(question)
        if is_cross:
            trace.record(
                "multi_condition_tripwire",
                TraceStatus.TERMINAL,
                spec_section="§5.3",
                detail=f"question spans conditions: {', '.join(groups)}",
            )
            return Decision.escalate(
                "Question spans multiple clinical conditions — transferring to your doctor.",
                scope=ScopeCategory.CROSS_CONDITION,
                risk=0.95,
                trace=trace,
            )

        # -- Pre-LLM triage: small talk → nudge, never escalate ---------------
        # A greeting/pleasantry is not a clinical question; without this it would
        # be classified out-of-scope and escalated to the doctor. Runs *after* the
        # red-flag rail so "hey, I have chest pain" still escalates correctly.
        if is_small_talk(question):
            trace.record(
                "conversational_rail",
                TraceStatus.TERMINAL,
                spec_section="§5.1",
                detail="non-clinical small talk — answering conversationally",
            )
            return Decision.clarify(
                "Hi! I can help with questions about your medicines, diet, or the "
                "care instructions your doctor approved. What would you like to know?",
                scope=ScopeCategory.ADMINISTRATIVE,
                trace=trace,
            )

        # -- Retrieve the currently-valid slice for this patient + now --------
        valid_slice = patient.valid_slice(now)

        # -- Reasoner: propose a grounded candidate (fail closed) -------------
        try:
            proposal = self._reasoner.propose(question=question, context=valid_slice)
        except ReasonerUnavailable:
            trace.record(
                "reasoner",
                TraceStatus.TERMINAL,
                detail="reasoner unavailable — fail closed",
            )
            return Decision.escalate(
                "Unable to process your question safely — transferring to your doctor.",
                trace=trace,
            )

        # -- Verifier: only when there is a real candidate to check -----------
        verification = None
        if proposal.is_answerable:
            try:
                verification = self._verifier.verify(
                    question=question,
                    proposal=proposal,
                    context=valid_slice,
                )
            except ReasonerUnavailable:
                trace.record(
                    "verifier",
                    TraceStatus.TERMINAL,
                    detail="verifier unavailable — fail closed",
                )
                return Decision.escalate(
                    "Unable to verify an answer safely — transferring to your doctor.",
                    trace=trace,
                )

        # -- 5-gate chain: the deterministic verdict --------------------------
        ctx = GateContext(
            question=question,
            proposal=proposal,
            verification=verification,
            valid_slice=valid_slice,
            thresholds=self._thresholds,
            now=now,
            call_session=session,
            trace=trace,
        )
        return run_gate_chain(ctx)


__all__ = ["Brain"]
