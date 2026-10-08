"""AuthService tests (NR-5).

Pins JWT round-trip, expiry/tamper rejection, internal-key verification, and
production guards on auth secrets.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from careline.adapters.auth.internal_key import KeyInvalid
from careline.adapters.auth.jwt import TokenInvalid
from careline.adapters.auth.principals import InternalPrincipal
from careline.config import Settings
from careline.services.auth_service import AuthService


def _auth() -> AuthService:
    return AuthService(settings=Settings())


def test_issue_and_decode_doctor_token():
    svc = _auth()
    token = svc.issue_doctor_token("dr-A")
    principal = svc.authenticate_doctor(token)
    assert principal.doctor_id == "dr-A"


def test_decode_expired_token_raises_token_invalid():
    settings = Settings()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "dr-A",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(TokenInvalid):
        AuthService(settings=settings).authenticate_doctor(token)


def test_decode_tampered_token_raises_token_invalid():
    svc = _auth()
    token = svc.issue_doctor_token("dr-A")
    # Flip a MIDDLE character: swapping the final base64url char can decode to
    # identical bytes (the last char carries only 4 significant bits), which
    # made this test flake — a "tampered" token that was byte-identical.
    mid = len(token) // 2
    flipped = "a" if token[mid] != "a" else "b"
    tampered = token[:mid] + flipped + token[mid + 1 :]
    if tampered == token:  # pragma: no cover - defensive
        tampered = token + "x"
    with pytest.raises(TokenInvalid):
        svc.authenticate_doctor(tampered)


def test_authenticate_internal_valid_key():
    settings = Settings()
    svc = AuthService(settings=settings)
    principal = svc.authenticate_internal(settings.internal_api_key)
    assert isinstance(principal, InternalPrincipal)


def test_authenticate_internal_wrong_key_raises_key_invalid():
    svc = _auth()
    with pytest.raises(KeyInvalid):
        svc.authenticate_internal("wrong-key")


def test_assert_prod_safe_rejects_default_jwt_secret_in_production(monkeypatch):
    monkeypatch.setenv("CARELINE_ENVIRONMENT", "production")
    settings = Settings()
    with pytest.raises(ValueError, match="jwt_secret"):
        settings.assert_prod_safe()


def test_assert_prod_safe_rejects_default_internal_api_key_in_production(monkeypatch):
    monkeypatch.setenv("CARELINE_ENVIRONMENT", "production")
    monkeypatch.setenv("CARELINE_JWT_SECRET", "prod-jwt-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("CARELINE_PIN_HMAC_SECRET", "prod-pin-hmac-secret-32bytes-min!!")
    settings = Settings()
    with pytest.raises(ValueError, match="internal_api_key"):
        settings.assert_prod_safe()


# --- REVIEW-2/3: doctor credential + role claims -----------------------------


def test_doctor_token_carries_doctor_role_claim():
    settings = Settings()
    token = AuthService(settings=settings).issue_doctor_token("dr-A")
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    assert payload["role"] == "doctor"


def test_patient_token_is_rejected_as_doctor_token():
    svc = _auth()
    token = svc.issue_patient_token(patient_id="p-1", doctor_id="dr-A")
    with pytest.raises(TokenInvalid):
        svc.authenticate_doctor(token)


def test_roleless_token_is_rejected_as_doctor_token():
    """A signed token with no role claim is not a doctor session (fail closed)."""
    settings = Settings()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": "dr-A", "iat": now, "exp": now + timedelta(hours=1)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(TokenInvalid):
        AuthService(settings=settings).authenticate_doctor(token)


def test_doctor_token_is_rejected_as_patient_token():
    svc = _auth()
    token = svc.issue_doctor_token("dr-A")
    with pytest.raises(TokenInvalid):
        svc.authenticate_patient(token)


def test_verify_doctor_credentials(monkeypatch):
    monkeypatch.setenv("CARELINE_DOCTOR_PASSWORD", "correct-horse-battery-staple")
    svc = AuthService(settings=Settings())
    assert svc.verify_doctor_credentials(
        doctor_id="dr-A", password="correct-horse-battery-staple"
    )
    assert not svc.verify_doctor_credentials(doctor_id="dr-A", password="wrong")
    assert not svc.verify_doctor_credentials(doctor_id="dr-A", password="")
    # Reserved anonymous-demo tenant can never sign in.
    assert not svc.verify_doctor_credentials(
        doctor_id="demo-doctor", password="correct-horse-battery-staple"
    )


def test_verify_doctor_credentials_honours_allowlist(monkeypatch):
    monkeypatch.setenv("CARELINE_DOCTOR_PASSWORD", "correct-horse-battery-staple")
    monkeypatch.setenv("CARELINE_DOCTOR_IDS", "dr-asha")
    svc = AuthService(settings=Settings())
    assert svc.verify_doctor_credentials(
        doctor_id="dr-asha", password="correct-horse-battery-staple"
    )
    assert not svc.verify_doctor_credentials(
        doctor_id="dr-evil", password="correct-horse-battery-staple"
    )
