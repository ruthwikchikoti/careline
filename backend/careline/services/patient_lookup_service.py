"""Caller-id + HMAC-PIN patient lookup for the voice call path (NR-5).

Resolves a patient under one doctor from telephony caller-id and a PIN. Unknown
caller and wrong PIN both raise :class:`PatientNotFound` — no oracle between them.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import hashlib
import hmac

from careline.config import Settings
from careline.domain.model.patient import PatientIdentity
from careline.domain.ports.repositories import PatientRepository


class PatientNotFound(LookupError):
    """Raised when caller-id is unknown or the PIN does not match."""


class PatientLookupService:
    """Lookup a patient by caller-id and verify their PIN."""

    def __init__(self, *, patient_repo: PatientRepository, settings: Settings) -> None:
        self._patient_repo = patient_repo
        self._settings = settings

    async def lookup(
        self, *, doctor_id: str, caller_id: str, pin: str
    ) -> PatientIdentity:
        """Resolve patient identity for an inbound call."""
        identity = await self._patient_repo.find_by_caller(
            doctor_id=doctor_id, caller_id=caller_id
        )
        if identity is None:
            raise PatientNotFound("patient not found")
        expected = identity.pin_hmac
        provided = hash_pin(pin=pin, secret=self._settings.pin_hmac_secret)
        if not hmac.compare_digest(provided, expected):
            raise PatientNotFound("patient not found")
        return identity


def hash_pin(*, pin: str, secret: str) -> str:
    """HMAC-SHA256 hex digest of a PIN under the server secret."""
    return hmac.new(secret.encode(), pin.encode(), hashlib.sha256).hexdigest()


__all__ = ["PatientLookupService", "PatientNotFound", "hash_pin"]
