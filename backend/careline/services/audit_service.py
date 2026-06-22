"""Audit logging — turn/call/event records for observability (VI-7).

Every live turn is logged with enough structure to reconstruct *what* happened
(which verdict, which gate fired) without retaining clinical text longer than
necessary.  :meth:`AuditService.redact_patient` implements DPDP erasure: clinical
text is nulled but the audit skeleton (ids, timestamps, verdicts, trace steps)
is retained for compliance.

Owner: Vinay (scope ``eval``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.enums import Verdict
from careline.domain.model.decision import Decision, ReasoningTrace


class AuditEventKind(str, Enum):
    """Categories of audit events beyond turn/call boundaries."""

    SYSTEM = "system"
    ESCALATION = "escalation"
    CONSENT = "consent"
    ERASURE = "erasure"
    EVAL = "eval"


class AuditTurnRecord(BaseModel):
    """One logged question turn — the primary audit unit."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    call_id: str
    patient_id: str
    doctor_id: str
    logged_at: datetime
    verdict: Verdict
    question: str | None = None
    answer_text: str | None = None
    escalation_reason: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    trace_steps: list[dict[str, Any]] = Field(default_factory=list)
    redacted: bool = False


class AuditCallRecord(BaseModel):
    """Summary record for an entire call."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    patient_id: str
    doctor_id: str
    started_at: datetime
    ended_at: datetime | None = None
    turn_count: int = 0
    final_verdict: Verdict | None = None
    escalated: bool = False
    redacted: bool = False


class AuditEventRecord(BaseModel):
    """Generic audit event (consent, erasure, eval run, etc.)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    kind: AuditEventKind
    logged_at: datetime
    patient_id: str | None = None
    doctor_id: str | None = None
    detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _trace_to_skeleton(trace: ReasoningTrace) -> list[dict[str, Any]]:
    """Serialise trace steps without clinical content — safe for redacted logs."""
    return [
        {
            "name": step.name,
            "status": step.status.value,
            "spec_section": step.spec_section,
            "detail": step.detail,
        }
        for step in trace.steps
    ]


class AuditService:
    """In-memory audit store for offline/MVP; Mongo persistence is NR-layer."""

    def __init__(self) -> None:
        self._turns: list[AuditTurnRecord] = []
        self._calls: dict[str, AuditCallRecord] = {}
        self._events: list[AuditEventRecord] = []

    @property
    def turns(self) -> tuple[AuditTurnRecord, ...]:
        return tuple(self._turns)

    @property
    def events(self) -> tuple[AuditEventRecord, ...]:
        return tuple(self._events)

    def log_turn(
        self,
        *,
        call_id: str,
        patient_id: str,
        doctor_id: str,
        question: str,
        decision: Decision,
        logged_at: datetime | None = None,
    ) -> AuditTurnRecord:
        """Record one question turn and its terminal decision."""
        record = AuditTurnRecord(
            turn_id=str(uuid.uuid4()),
            call_id=call_id,
            patient_id=patient_id,
            doctor_id=doctor_id,
            logged_at=logged_at or datetime.now(timezone.utc),
            verdict=decision.verdict,
            question=question,
            answer_text=decision.answer_text,
            escalation_reason=decision.escalation_reason,
            confidence=decision.confidence,
            risk=decision.risk,
            trace_steps=_trace_to_skeleton(decision.trace),
        )
        self._turns.append(record)

        if call_id in self._calls:
            call = self._calls[call_id]
            self._calls[call_id] = call.model_copy(
                update={
                    "turn_count": call.turn_count + 1,
                    "final_verdict": decision.verdict,
                    "escalated": call.escalated or decision.verdict is Verdict.ESCALATE,
                }
            )
        return record

    def log_call(
        self,
        *,
        call_id: str,
        patient_id: str,
        doctor_id: str,
        started_at: datetime | None = None,
    ) -> AuditCallRecord:
        """Open a call record (idempotent — returns existing if already logged)."""
        if call_id in self._calls:
            return self._calls[call_id]
        record = AuditCallRecord(
            call_id=call_id,
            patient_id=patient_id,
            doctor_id=doctor_id,
            started_at=started_at or datetime.now(timezone.utc),
        )
        self._calls[call_id] = record
        return record

    def end_call(
        self,
        call_id: str,
        *,
        ended_at: datetime | None = None,
    ) -> AuditCallRecord | None:
        """Close a call record."""
        call = self._calls.get(call_id)
        if call is None:
            return None
        updated = call.model_copy(update={"ended_at": ended_at or datetime.now(timezone.utc)})
        self._calls[call_id] = updated
        return updated

    def log_event(
        self,
        kind: AuditEventKind,
        *,
        patient_id: str | None = None,
        doctor_id: str | None = None,
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
        logged_at: datetime | None = None,
    ) -> AuditEventRecord:
        """Record a non-turn audit event."""
        record = AuditEventRecord(
            event_id=str(uuid.uuid4()),
            kind=kind,
            logged_at=logged_at or datetime.now(timezone.utc),
            patient_id=patient_id,
            doctor_id=doctor_id,
            detail=detail,
            metadata=dict(metadata or {}),
        )
        self._events.append(record)
        return record

    def turns_for_call(self, call_id: str) -> list[AuditTurnRecord]:
        return [t for t in self._turns if t.call_id == call_id]

    def turns_for_patient(self, patient_id: str) -> list[AuditTurnRecord]:
        return [t for t in self._turns if t.patient_id == patient_id]

    def calls_for_doctor(self, doctor_id: str) -> list[AuditCallRecord]:
        return [c for c in self._calls.values() if c.doctor_id == doctor_id]

    def get_call(self, call_id: str) -> AuditCallRecord | None:
        return self._calls.get(call_id)

    def redact_patient(self, patient_id: str) -> int:
        """DPDP erasure — null clinical text, keep audit skeleton.

        Returns the number of records redacted (turns + calls).
        """
        count = 0
        redacted_turns: list[AuditTurnRecord] = []
        for turn in self._turns:
            if turn.patient_id != patient_id or turn.redacted:
                redacted_turns.append(turn)
                continue
            redacted_turns.append(
                turn.model_copy(
                    update={
                        "question": None,
                        "answer_text": None,
                        "escalation_reason": None,
                        "redacted": True,
                    }
                )
            )
            count += 1
        self._turns = redacted_turns

        for call_id, call in list(self._calls.items()):
            if call.patient_id == patient_id and not call.redacted:
                self._calls[call_id] = call.model_copy(update={"redacted": True})
                count += 1

        self.log_event(
            AuditEventKind.ERASURE,
            patient_id=patient_id,
            detail=f"redacted {count} audit record(s) — clinical text nulled",
        )
        return count


__all__ = [
    "AuditEventKind",
    "AuditTurnRecord",
    "AuditCallRecord",
    "AuditEventRecord",
    "AuditService",
]
