"""Patient wire shapes — no pin_hmac or clinical payloads (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from careline.domain.model.fact import Fact


#: New registrations: exactly six ASCII digits (1M combinations, matching the
#: portal's 6-digit field). Login still accepts whatever an existing identity was
#: registered with, so pre-policy identities keep working.
PIN_PATTERN = r"^[0-9]{6}$"


class PatientRegisterIn(BaseModel):
    """Register a patient identity — plain PIN is hashed server-side, never stored."""

    model_config = ConfigDict(extra="forbid")

    patient_id: str
    caller_id: str
    pin: str = Field(pattern=PIN_PATTERN, description="Exactly 6 digits.")


class PatientOut(BaseModel):
    """Patient summary — wrong-tenant requests never reach this shape."""

    model_config = ConfigDict(extra="forbid")

    patient_id: str
    doctor_id: str
    fact_count: int


class ErasureOut(BaseModel):
    """Outcome of DPDP right-to-erasure — no clinical payloads."""

    model_config = ConfigDict(extra="forbid")

    patient_id: str
    layer1_nulled: int
    layer2_dropped: bool
    audit_redacted: int


class FactOut(BaseModel):
    """One clinical fact for the Patient Record screen, with its temporal stamps.

    Flat read shape: the doctor-approved ``summary`` plus the half-open validity
    window and approval stamps the timeline renders. ``current`` says whether the
    fact is in the valid slice *now* (vs retired/superseded).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    summary: str
    effective_from: datetime
    superseded_at: datetime | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    current: bool

    @classmethod
    def from_fact(cls, fact: Fact, *, current: bool) -> "FactOut":
        return cls(
            id=fact.id,
            kind=fact.kind.value,
            summary=fact.summary,
            effective_from=fact.validity.effective_from,
            superseded_at=fact.validity.superseded_at,
            approved_by=fact.approved_by,
            approved_at=fact.approved_at,
            current=current,
        )


class PatientRecordOut(BaseModel):
    """The longitudinal record for one patient: valid slice now + retired history.

    ``current`` is the approved, currently-valid facts (the answerable context);
    ``history`` is the facts superseded as of ``as_of`` — kept for the timeline,
    never surfaced as current truth.
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: str
    doctor_id: str
    as_of: datetime
    current: list[FactOut]
    history: list[FactOut]
