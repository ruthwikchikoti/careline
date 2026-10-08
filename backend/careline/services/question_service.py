"""QuestionService — the brain endpoint use-case (VI-6).

Thin application wrapper around the safety spine: delegates the verdict to
Ruthwik's compiled LangGraph when injected, otherwise to the headless
``Brain`` — the *same* single safety authority the graph wraps. There is no
third decision path: the service owns session/audit/telephony concerns and
never re-implements rail, retrieval, or gate logic itself (the duplicated
inline pipeline this replaced had already drifted: no small-talk rail, no
retrieval narrowing).

Manages per-call ``CallSession`` state (clarify-turn budget) and hands
ESCALATE verdicts to the telephony port. Naresh's ``/internal/run-question``
router calls this service.

Observability (never decides, never breaks a call): each turn runs inside a
usage ``turn_scope``; its real wall-clock latency and that per-turn usage go
to the Langfuse turn trace (opened with ``begin_turn`` at the turn's real
start) and to the online monitor (``online_monitor.record``), whose sampled
async judge runs in a copy of this turn's context. A pipeline that raises is
recorded as an error turn and the exception propagates unchanged.

Owner: Priyanshu (scope ``safety``).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from careline.adapters.llm import usage as usage_recorder
from careline.adapters.llm.tracing import trace_span
from careline.adapters.observability import begin_turn, record_turn
from careline.adapters.telephony.stub import EscalationPayload, TelephonyPort, TelephonyStub
from careline.domain.brain.brain import Brain
from careline.domain.enums import Verdict
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.domain.model.patient import Patient
from careline.domain.ports.reasoning import Reasoner, Verifier
from careline.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds
from careline.services import online_monitor
from careline.services.audit_service import AuditEventKind, AuditService, AuditTurnRecord

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
            self._brain = None
        elif reasoner is not None and verifier is not None:
            self._graph = None
            self._brain = Brain(reasoner=reasoner, verifier=verifier, thresholds=thresholds)
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
        on_audit_turn: Callable[[AuditTurnRecord], None] | None = None,
    ) -> Decision:
        """Process one question and return the terminal ``Decision``.

        ``on_audit_turn`` (optional) receives the logged audit record, so a caller
        such as the patient portal can hand the ``turn_id`` back for feedback.
        """
        now = now or datetime.now(timezone.utc)
        started = time.perf_counter()
        turn_trace = begin_turn()
        session.record_turn()
        trace = ReasoningTrace()

        if self._audit is not None:
            self._audit.log_call(
                call_id=session.call_id,
                patient_id=session.patient_id,
                doctor_id=session.doctor_id,
            )

        with trace_span("question_service.run_question") as span, \
                usage_recorder.turn_scope() as turn_usage:
            span.log_input(question=question, patient_id=patient.patient_id)
            span.log_metadata(
                call_id=session.call_id,
                doctor_id=session.doctor_id,
            )

            try:
                decision = self._run_pipeline(
                    question=question,
                    patient=patient,
                    session=session,
                    now=now,
                    trace=trace,
                )
            except Exception:
                failed_ms = (time.perf_counter() - started) * 1000.0
                online_monitor.record_error(latency_ms=failed_ms)
                record_turn(  # closes a v3 generation opened at the turn's start
                    question=question,
                    patient_id=patient.patient_id,
                    verdict="error",
                    scope="error",
                    latency_ms=failed_ms,
                    turn_usage=turn_usage,
                    trace=turn_trace,
                )
                raise

            if decision.verdict is Verdict.CLARIFY:
                session.record_clarify()
            elif decision.verdict is Verdict.ESCALATE:
                self._deliver_escalation(decision, session)

            if self._audit is not None:
                logged = self._audit.log_turn(
                    call_id=session.call_id,
                    patient_id=session.patient_id,
                    doctor_id=session.doctor_id,
                    question=question,
                    decision=decision,
                )
                if on_audit_turn is not None:
                    on_audit_turn(logged)
                if decision.verdict is Verdict.ESCALATE:
                    self._audit.log_event(
                        AuditEventKind.ESCALATION,
                        patient_id=session.patient_id,
                        doctor_id=session.doctor_id,
                        detail=decision.escalation_reason,
                    )

            span.log_output(verdict=decision.verdict.value)
            latency_ms = (time.perf_counter() - started) * 1000.0
            record_turn(
                question=question,
                patient_id=patient.patient_id,
                model="deterministic-spine",  # only used when the turn made no LLM call
                verdict=decision.verdict.value,
                scope=decision.scope.value if decision.scope else "unscoped",
                latency_ms=latency_ms,
                turn_usage=turn_usage,
                trace=turn_trace,
            )
            # Inside the turn scope: a sampled judge inherits this turn's context.
            online_monitor.record(
                decision=decision,
                question=question,
                latency_ms=latency_ms,
                cost=turn_usage,
                patient=patient,
                now=now,
            )
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

        # Inline path: the Brain IS the single safety authority — the service
        # adds session/audit/telephony concerns around it, never a second
        # decision path. Parity with the graph path is asserted in
        # tests/brain/test_parity_question_service.py.
        return self._brain.run_question(
            question=question,
            patient=patient,
            now=now,
            session=session,
            trace=trace,
        )

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
