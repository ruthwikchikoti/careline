"""Telephony escalation sink — stub for the MVP (VI-6).

The port a real telephony adapter (Twilio/Exotel) would implement.  For the
capstone this is an in-memory stub that records transfers; the important thing
is that the *interface* is defined so ``QuestionService`` can escalate without
knowing the transport.

Owner: Vinay (scope ``safety``).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class EscalationPayload(BaseModel):
    """Structured payload handed to the telephony layer on ESCALATE."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    patient_id: str
    doctor_id: str
    reason: str
    escalated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    terminal_gate: str | None = Field(
        default=None,
        description="The rail/gate that forced escalation, for the handoff brief.",
    )


class TelephonyPort(ABC):
    """Abstract escalation sink — live transfer to the human doctor."""

    @abstractmethod
    def escalate(self, payload: EscalationPayload) -> None:
        """Initiate a live transfer to the doctor."""
        raise NotImplementedError


class TelephonyStub(TelephonyPort):
    """In-memory stub that records escalations for tests and offline demos."""

    def __init__(self) -> None:
        self.escalations: list[EscalationPayload] = []

    def escalate(self, payload: EscalationPayload) -> None:
        logger.info(
            "ESCALATE call=%s patient=%s reason=%s",
            payload.call_id,
            payload.patient_id,
            payload.reason,
        )
        self.escalations.append(payload)

    def last(self) -> EscalationPayload | None:
        """Most recent escalation, if any."""
        return self.escalations[-1] if self.escalations else None


__all__ = ["EscalationPayload", "TelephonyPort", "TelephonyStub"]
