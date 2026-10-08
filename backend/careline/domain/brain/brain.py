"""The Brain — CareLine's single headless safety authority (RU-3).

One question in, one terminal :class:`Decision` out. The Brain runs the safety
spine end to end:

    red-flag rail → acute-concern net → symptom-report layer → multi-condition tripwire
    → (hypothetical-only danger: CLARIFY) → valid slice → reasoner
    → (lazy) verifier → 5-gate chain → Decision + reasoning trace

It is deliberately **headless**: no telephony, no audit, no session mutation, no
tracing spans. Those are application concerns that wrap the Brain (Priyanshu's
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

from careline.domain.enums import TraceStatus
from careline.domain.gates.chain import GateContext, run_gate_chain
from careline.domain.gates.grounding import non_current_facts
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.domain.model.patient import Patient
from careline.domain.ports.reasoning import Reasoner, ReasonerUnavailable, Verifier
from careline.domain.brain.triage import run_triage
from careline.domain.retrieval import retrieval_detail, retrieve_relevant
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

        # -- Pre-LLM triage (shared with the graph's triage node) -----------
        # red-flag rail → acute-concern net → multi-condition tripwire → small
        # talk. v4: both emergency nets run on EVERY question, so an emergency
        # mixed into an in-scope question never reaches the Reasoner.
        triaged = run_triage(question, trace)
        if triaged is not None:
            return triaged

        # -- Retrieve the currently-valid slice for this patient + now --------
        valid_slice = patient.valid_slice(now)

        # -- Retrieval-augmented grounding: rank the valid facts by relevance and
        # ground the reasoner on the most relevant subset. Ranking over the valid
        # slice means every grounded fact is already source-of-truth-validated; a
        # small record passes through unchanged (parity-stable). The gate chain
        # below still sees the *full* valid slice for its citation-validity check.
        retrieval = retrieve_relevant(question=question, valid_slice=valid_slice)
        grounding = retrieval.grounding
        trace.record(
            "retrieval",
            TraceStatus.PASS,
            spec_section="§4.2",
            detail=retrieval_detail(retrieval),
        )

        # -- Reasoner: propose a grounded candidate (fail closed) -------------
        try:
            proposal = self._reasoner.propose(question=question, context=grounding)
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
        # The verifier is the INDEPENDENT backstop, so it checks against the FULL valid
        # slice (not the narrowed grounding) — a fact retrieval trimmed from the reasoner
        # is still seen here. The graph node mirrors this for parity.
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
            non_current_facts=non_current_facts(patient, now),
            thresholds=self._thresholds,
            now=now,
            call_session=session,
            trace=trace,
        )
        return run_gate_chain(ctx)


__all__ = ["Brain"]
