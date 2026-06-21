"""QuestionService — the brain endpoint use-case (VI-6).

Thin application wrapper around the safety spine: delegates the verdict to
Ruthwik's compiled LangGraph when injected, otherwise falls back to the inline
pipeline (offline tests / eval harness).

Manages per-call ``CallSession`` state (clarify-turn budget) and hands
ESCALATE verdicts to the telephony port. Naresh's ``/internal/run-question``
router calls this service.

Owner: Vinay (scope ``safety``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from careline.adapters.llm.tracing import trace_span
from careline.adapters.telephony.stub import EscalationPayload, TelephonyPort, TelephonyStub
from careline.domain.enums import ScopeCategory, TraceStatus, Verdict
from careline.domain.gates.chain import GateContext, run_gate_chain
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.domain.model.patient import Patient
from careline.domain.ports.reasoning import Reasoner, ReasonerUnavailable, Verifier
from careline.domain.rails.red_flag import check_multi_condition, check_red_flag
from careline.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds
from careline.services.audit_service import AuditEventKind, AuditService

if TYPE_CHECKING:
    from careline.adapters.orchestration.graph import CompiledBrainGraph


class QuestionService:
    """Run one question through the safety spine for a single patient call."""

    def __init__(
        self,
        *,
        reasoner: Reasoner | None = None,
        verifier: Verifier | None = None,
        graph: CompiledBrainGraph | None = None,
        telephony: TelephonyPort | None = None,
        thresholds: Thresholds | None = None,
        audit: AuditService | None = None,
    ) -> None:
        if graph is not None:
            self._graph = graph
            self._reasoner = None
            self._verifier = None
        elif reasoner is not None and verifier is not None:
            self._graph = None
            self._reasoner = reasoner
            self._verifier = verifier
        else:
            raise ValueError("QuestionService requires graph or (reasoner, verifier)")
        self._telephony = telephony or TelephonyStub()
        self._thresholds = thresholds or DEFAULT_THRESHOLDS
        self._audit = audit

    @property
    def telephony(self) -> TelephonyPort:
        return self._telephony

    def run_question(
        self,
        *,
        question: str,
        patient: Patient,
        session: CallSession,
        now: datetime | None = None,
    ) -> Decision:
        """Process one question and return the terminal ``Decision``."""
        now = now or datetime.now(timezone.utc)
        session.record_turn()
        trace = ReasoningTrace()

        if self._audit is not None:
            self._audit.log_call(
                call_id=session.call_id,
                patient_id=session.patient_id,
                doctor_id=session.doctor_id,
            )

        with trace_span("question_service.run_question") as span:
            span.log_input(question=question, patient_id=patient.patient_id)
            span.log_metadata(
                call_id=session.call_id,
                doctor_id=session.doctor_id,
            )

            decision = self._run_pipeline(
                question=question,
                patient=patient,
                session=session,
                now=now,
                trace=trace,
            )

            if decision.verdict is Verdict.CLARIFY:
                session.record_clarify()
            elif decision.verdict is Verdict.ESCALATE:
                self._deliver_escalation(decision, session)

            if self._audit is not None:
                self._audit.log_turn(
                    call_id=session.call_id,
                    patient_id=session.patient_id,
                    doctor_id=session.doctor_id,
                    question=question,
                    decision=decision,
                )
                if decision.verdict is Verdict.ESCALATE:
                    self._audit.log_event(
                        AuditEventKind.ESCALATION,
                        patient_id=session.patient_id,
                        doctor_id=session.doctor_id,
                        detail=decision.escalation_reason,
                    )

            span.log_output(verdict=decision.verdict.value)
            return decision

    def _run_pipeline(
        self,
        *,
        question: str,
        patient: Patient,
        session: CallSession,
        now: datetime,
        trace: ReasoningTrace,
    ) -> Decision:
        if self._graph is not None:
            return self._graph.run_question(
                question=question,
                patient=patient,
                now=now,
                session=session,
                trace=trace,
            )

        # -- Pre-LLM triage: red-flag rail ------------------------------------
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

        valid_slice = patient.valid_slice(now)

        # -- Reasoner ---------------------------------------------------------
        try:
            with trace_span("reasoner.propose") as rspan:
                rspan.log_input(question=question)
                proposal = self._reasoner.propose(question=question, context=valid_slice)
                rspan.log_output(scope=proposal.scope.value, answerable=proposal.is_answerable)
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

        # -- Verifier (only when there is a candidate to check) ---------------
        verification = None
        if proposal.is_answerable:
            try:
                with trace_span("verifier.verify") as vspan:
                    vspan.log_input(citations=list(proposal.citations))
                    verification = self._verifier.verify(
                        question=question,
                        proposal=proposal,
                        context=valid_slice,
                    )
                    vspan.log_output(supported=verification.supported)
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

        # -- Gate chain -------------------------------------------------------
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

    def _deliver_escalation(self, decision: Decision, session: CallSession) -> None:
        terminal = decision.trace.terminal_step
        payload = EscalationPayload(
            call_id=session.call_id,
            patient_id=session.patient_id,
            doctor_id=session.doctor_id,
            reason=decision.escalation_reason or "Escalated to doctor",
            terminal_gate=terminal.name if terminal else None,
        )
        self._telephony.escalate(payload)


__all__ = ["QuestionService"]
