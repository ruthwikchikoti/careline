"""Consultation wire shapes — no doctor_id in request bodies (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from careline.domain.model.consultation import ConsultationStatus


class ConsultationCreateIn(BaseModel):
    """Open a draft consultation — ``doctor_id`` comes from the JWT principal."""

    model_config = ConfigDict(extra="forbid")

    patient_id: str
    transcript: str | None = None


class ConsentIn(BaseModel):
    """Stamp explicit patient consent on a consultation."""

    model_config = ConfigDict(extra="forbid")

    purpose: str


class ConsultationOut(BaseModel):
    """Consultation summary — no transcript or fact payloads."""

    model_config = ConfigDict(extra="forbid")

    consultation_id: str
    doctor_id: str
    patient_id: str
    status: ConsultationStatus
    created_at: datetime
    fact_count: int


class ExtractOut(BaseModel):
    """Outcome of transcript extraction."""

    model_config = ConfigDict(extra="forbid")

    consultation_id: str
    fact_count: int
    status: ConsultationStatus


class ApprovalOut(BaseModel):
    """Outcome of one-tap HITL approval."""

    model_config = ConfigDict(extra="forbid")

    consultation_id: str
    status: ConsultationStatus
    applied_facts: int
    retired_facts: int
