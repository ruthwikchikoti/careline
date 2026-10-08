"""Auth wire shapes — no clinical fields (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """Doctor login — a JWT is issued only against the deployment credential."""

    model_config = ConfigDict(extra="forbid")

    doctor_id: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    """Bearer token issued after demo login."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
