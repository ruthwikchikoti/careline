"""Doctor JWT + internal API key authentication (NR-5).

Thin orchestrator over :mod:`careline.adapters.auth` — routers depend on this
service, not on JWT primitives directly.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import hmac
from functools import lru_cache

from careline.adapters.auth.hash_password import (
    burn_equivalent_work,
    hash_password,
    verify_password,
)
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

# Ids owned by the anonymous Live-Console demo (``/demo/ask``). Nobody may sign in
# as the demo tenant or register the demo patient, so anonymous demo turns are
# structurally unreachable from any doctor console or patient portal (REVIEW-1/2).
DEMO_DOCTOR_ID = "demo-doctor"
DEMO_PATIENT_ID = "demo-patient"
RESERVED_DOCTOR_IDS = frozenset({DEMO_DOCTOR_ID})
RESERVED_PATIENT_IDS = frozenset({DEMO_PATIENT_ID})


@lru_cache(maxsize=8)
def _dummy_hash(template: str) -> str:
    """A throwaway hash with the same cost parameters as ``template``.

    Verifying an unknown doctor id against it costs the same as a real check,
    so timing does not reveal which ids have a credential configured.
    """
    parts = template.split("$")
    if parts[0] == "pbkdf2_sha256" and len(parts) == 4 and parts[1].isdigit():
        return hash_password("careline-unknown-doctor", iterations=int(parts[1]))
    return template


def _normalise(identifier: str) -> str:
    return identifier.strip().casefold()


def is_reserved_doctor_id(doctor_id: str) -> bool:
    """True for ids reserved by the anonymous demo (case/space-insensitive)."""
    return _normalise(doctor_id) in RESERVED_DOCTOR_IDS


def is_reserved_patient_id(patient_id: str) -> bool:
    """True for patient ids reserved by the anonymous demo (case/space-insensitive)."""
    return _normalise(patient_id) in RESERVED_PATIENT_IDS


class AuthService:
    """Issue and validate doctor/patient JWTs and internal service keys."""

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    def verify_doctor_credentials(self, *, doctor_id: str, password: str) -> bool:
        """Check a doctor login against *that doctor's* credential (REVIEW-2, SECURITY-2).

        With ``CARELINE_DOCTOR_CREDENTIALS`` set, each doctor id has its own
        salted hash and only listed ids can sign in; the shared password is then
        ignored entirely (it must never be a backdoor next to per-doctor hashes).
        Without it, the shared ``CARELINE_DOCTOR_PASSWORD`` is the explicit
        dev/local-demo fallback — and a hardened deploy (production / public
        demo) never accepts it, even if the startup guard were bypassed.

        Fails closed: an empty id/password, a reserved demo id, or an id outside
        the optional ``CARELINE_DOCTOR_IDS`` allowlist is refused. The credential
        compare is constant-time and always runs (an unknown id burns the same
        hashing work), so timing does not reveal which check failed.
        """
        matches = self._credential_matches(doctor_id=doctor_id, password=password)
        if not doctor_id.strip() or not password:
            return False
        if is_reserved_doctor_id(doctor_id):
            return False
        allowlist = self._settings.doctor_id_allowlist
        if allowlist and doctor_id not in allowlist:
            return False
        return matches

    def _credential_matches(self, *, doctor_id: str, password: str) -> bool:
        table = self._settings.doctor_credential_map
        if table:
            encoded = table.get(doctor_id)
            if encoded is None:
                burn_equivalent_work(_dummy_hash(next(iter(table.values()))), password)
                return False
            return verify_password(password, encoded)
        expected = self._settings.doctor_password
        matches = hmac.compare_digest(password.encode(), expected.encode())
        if self._settings.requires_hardened_config or not expected:
            return False  # shared password is a dev-only fallback (SECURITY-2)
        return matches

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


__all__ = [
    "AuthService",
    "DEMO_DOCTOR_ID",
    "DEMO_PATIENT_ID",
    "RESERVED_DOCTOR_IDS",
    "RESERVED_PATIENT_IDS",
    "is_reserved_doctor_id",
    "is_reserved_patient_id",
]
