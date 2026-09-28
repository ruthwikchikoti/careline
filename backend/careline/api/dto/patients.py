"""Patient wire shapes — no pin_hmac or clinical payloads (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PatientRegisterIn(BaseModel):
    """Register a patient identity — plain PIN is hashed server-side, never stored."""

    model_config = ConfigDict(extra="forbid")

    patient_id: str
    caller_id: str
    pin: str = Field(min_length=4, max_length=12)


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
