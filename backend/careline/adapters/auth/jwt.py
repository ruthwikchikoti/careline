"""JWT encode/decode for doctor sessions (NR-5).

PyJWT is imported lazily so the offline suite can skip the dependency until auth
extras are installed.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from careline.adapters.auth.principals import DoctorPrincipal


class TokenInvalid(ValueError):
    """Raised when a JWT is missing, expired, tampered, or otherwise invalid."""


def encode_doctor_token(*, doctor_id: str, secret: str, ttl_seconds: int) -> str:
    """Issue an HS256 JWT with ``sub = doctor_id``."""
    jwt = _import_jwt()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": doctor_id,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_doctor_token(token: str, secret: str) -> DoctorPrincipal:
    """Validate a doctor JWT and return the authenticated principal."""
    jwt = _import_jwt()
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise TokenInvalid("invalid or expired token") from exc
    doctor_id = payload.get("sub")
    if not doctor_id or not isinstance(doctor_id, str):
        raise TokenInvalid("token missing subject")
    return DoctorPrincipal(doctor_id=doctor_id)


def _import_jwt():
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - exercised only without PyJWT
        raise RuntimeError(
            "PyJWT is required for auth; install careline[auth]"
        ) from exc
    return jwt


__all__ = ["TokenInvalid", "decode_doctor_token", "encode_doctor_token"]
