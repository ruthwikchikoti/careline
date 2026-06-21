"""Patient wire shapes — no pin_hmac or clinical payloads (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PatientOut(BaseModel):
    """Patient summary — wrong-tenant requests never reach this shape."""

    model_config = ConfigDict(extra="forbid")

    patient_id: str
    doctor_id: str
    fact_count: int
