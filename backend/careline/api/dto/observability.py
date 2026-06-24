"""Observability wire shapes — audit / escalations / eval reads (VI-7 · #5).

Doctor-scoped read projections over :class:`AuditService` and the offline eval
re-run.  No clinical text beyond what the audit skeleton already retains; the
caller's own ``doctor_id`` is implied by the JWT and never echoed back.

Owner: Vinay (scope ``eval``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.enums import Verdict


class AuditTurnOut(BaseModel):
    """One logged question turn — the primary audit unit."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    call_id: str
    patient_id: str
    logged_at: datetime
    verdict: Verdict
    question: str | None = None
    answer_text: str | None = None
    escalation_reason: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    trace_steps: list[dict[str, Any]] = Field(default_factory=list)
    redacted: bool = False


class AuditCallOut(BaseModel):
    """Summary record for an entire call."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    patient_id: str
    started_at: datetime
    ended_at: datetime | None = None
    turn_count: int = 0
    final_verdict: Verdict | None = None
    escalated: bool = False
    redacted: bool = False


class AuditEventOut(BaseModel):
    """A non-turn audit event (consent, erasure, eval run, system)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    kind: str
    logged_at: datetime
    patient_id: str | None = None
    detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditLogOut(BaseModel):
    """Doctor-scoped audit trail — calls plus their turns, newest first."""

    model_config = ConfigDict(extra="forbid")

    calls: list[AuditCallOut] = Field(default_factory=list)
    turns: list[AuditTurnOut] = Field(default_factory=list)


class EscalationsOut(BaseModel):
    """Doctor-scoped human-handoff queue — turns that terminated in ESCALATE."""

    model_config = ConfigDict(extra="forbid")

    waiting: int = 0
    escalations: list[AuditTurnOut] = Field(default_factory=list)


class EvalScenarioOut(BaseModel):
    """One re-run T-scenario and whether it matched its expected verdict."""

    model_config = ConfigDict(extra="forbid")

    name: str
    verdict: Verdict
    passed: bool


class EvalRunOut(BaseModel):
    """Outcome of an offline eval re-run through the live safety spine."""

    model_config = ConfigDict(extra="forbid")

    passed: int
    total: int
    digest: str
    scenarios: list[EvalScenarioOut] = Field(default_factory=list)


__all__ = [
    "AuditTurnOut",
    "AuditCallOut",
    "AuditEventOut",
    "AuditLogOut",
    "EscalationsOut",
    "EvalScenarioOut",
    "EvalRunOut",
]
