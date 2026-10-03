"""Doctor JWT + internal API key authentication (NR-5).

Thin orchestrator over :mod:`careline.adapters.auth` — routers depend on this
service, not on JWT primitives directly.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from careline.adapters.auth.internal_key import verify_internal_key
from careline.adapters.auth.jwt import (
    decode_doctor_token,
    decode_patient_token,
    encode_doctor_token,
    encode_patient_token,
)
from careline.adapters.auth.principals import (
    DoctorPrincipal,
    InternalPrincipal,
    PatientPrincipal,
)
from careline.config import Settings


class AuthService:
    """Issue and validate doctor/patient JWTs and internal service keys."""

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    def issue_doctor_token(self, doctor_id: str) -> str:
        """Issue a signed JWT for a verified doctor."""
        return encode_doctor_token(
            doctor_id=doctor_id,
            secret=self._settings.jwt_secret,
            ttl_seconds=self._settings.jwt_ttl_seconds,
        )

    def authenticate_doctor(self, token: str) -> DoctorPrincipal:
        """Decode and validate a doctor JWT."""
        return decode_doctor_token(token, self._settings.jwt_secret)

    def issue_patient_token(self, *, patient_id: str, doctor_id: str) -> str:
        """Issue a signed JWT for a verified patient (portal session)."""
        return encode_patient_token(
            patient_id=patient_id,
            doctor_id=doctor_id,
            secret=self._settings.jwt_secret,
            ttl_seconds=self._settings.jwt_ttl_seconds,
        )

    def authenticate_patient(self, token: str) -> PatientPrincipal:
        """Decode and validate a patient JWT."""
        return decode_patient_token(token, self._settings.jwt_secret)

    def authenticate_internal(self, key: str) -> InternalPrincipal:
        """Verify the internal API key for service-to-service routes."""
        return verify_internal_key(key, self._settings.internal_api_key)


__all__ = ["AuthService"]
