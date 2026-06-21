"""Brain / run-question wire shapes (NR-6 phase 2).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.enums import Verdict


class QuestionIn(BaseModel):
    """Internal telephony bridge payload — trusted via ``X-Internal-Key``."""

    model_config = ConfigDict(extra="forbid")

    doctor_id: str
    patient_id: str
    call_id: str
    question: str


class TraceStepOut(BaseModel):
    """One explainable pipeline step in the API response."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: str
    spec_section: str | None = None
    detail: str | None = None


class AnswerOut(BaseModel):
    """Terminal decision for one patient question."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    answer_text: str | None = None
    escalation_reason: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    citations: list[str] = Field(default_factory=list)
    trace: list[TraceStepOut] = Field(default_factory=list)
