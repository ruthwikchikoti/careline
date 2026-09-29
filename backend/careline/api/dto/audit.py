"""Audit and eval wire shapes (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.enums import Verdict
from careline.services.audit_service import (
    AuditCallRecord,
    AuditEventKind,
    AuditEventRecord,
    AuditTurnRecord,
)


class AuditTurnOut(AuditTurnRecord):
    """One logged question turn for the doctor audit surface."""


class AuditCallOut(AuditCallRecord):
    """One call summary for the doctor audit surface."""


class AuditEventOut(AuditEventRecord):
    """Non-turn audit event (consent, erasure, eval run, etc.)."""


class EvalScenarioOut(BaseModel):
    """One offline eval scenario outcome."""

    model_config = ConfigDict(extra="forbid")

    name: str
    verdict: Verdict
    passed: bool


class EvalRunOut(BaseModel):
    """Offline eval re-run results plus human-readable digest."""

    model_config = ConfigDict(extra="forbid")

    passed: int = Field(ge=0)
    total: int = Field(ge=0)
    digest: str
    scenarios: list[EvalScenarioOut] = Field(default_factory=list)


__all__ = [
    "AuditCallOut",
    "AuditEventKind",
    "AuditEventOut",
    "AuditTurnOut",
    "EvalRunOut",
    "EvalScenarioOut",
]
