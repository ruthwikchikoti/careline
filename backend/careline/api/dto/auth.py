"""Auth wire shapes — no clinical fields (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    """Capstone demo login — issues a JWT for the given doctor id."""

    model_config = ConfigDict(extra="forbid")

    doctor_id: str


class TokenResponse(BaseModel):
    """Bearer token issued after demo login."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
