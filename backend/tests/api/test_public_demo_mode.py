"""Public-demo hardening mode — CARELINE_PUBLIC_DEMO=true (REVIEW-6).

The public deploy runs ``CARELINE_ENVIRONMENT=development`` so the demo console
stays mounted — which meant :meth:`Settings.assert_prod_safe` never ran and the
published dev-default secrets were accepted on a public URL. Public-demo mode
keeps the demo routes but applies the production secret guard at startup:
dev-default JWT / internal-key / PIN-HMAC secrets are refused, per-doctor
credentials (``CARELINE_DOCTOR_CREDENTIALS``) are required, and the shared
``CARELINE_DOCTOR_PASSWORD`` dev fallback is refused outright (SECURITY-2).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from careline.adapters.auth.hash_password import hash_password
from careline.api.app import create_app
from careline.config import Settings

_STRONG = {
    "CARELINE_JWT_SECRET": "public-demo-jwt-secret-at-least-32-bytes!!",
    "CARELINE_INTERNAL_API_KEY": "public-demo-internal-key-at-least-32-bytes",
    "CARELINE_PIN_HMAC_SECRET": "public-demo-pin-hmac-secret-32-bytes-min!",
    "CARELINE_DOCTOR_CREDENTIALS": (
        f"dr-asha:{hash_password('a-long-random-doctor-password', iterations=1_000)}"
    ),
}


@pytest.fixture()
def public_env(monkeypatch):
    import careline.api.app as app_module

    # Ignore any developer .env so only this test's environment counts.
    monkeypatch.setattr(app_module, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setenv("CARELINE_PUBLIC_DEMO", "true")
    for key in (*_STRONG, "CARELINE_DOCTOR_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _set_strong(monkeypatch, **overrides):
    for key, value in {**_STRONG, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_public_demo_is_off_by_default():
    settings = Settings(_env_file=None)
    assert settings.public_demo is False
    assert settings.requires_hardened_config is False
    settings.assert_prod_safe()  # dev: no-op


def test_public_demo_rejects_dev_default_jwt_secret(public_env):
    settings = Settings(_env_file=None)
    assert settings.requires_hardened_config is True
    assert settings.is_production is False  # demo routes stay mounted
    with pytest.raises(ValueError, match="jwt_secret"):
        settings.assert_prod_safe()


@pytest.mark.parametrize(
    ("missing", "match"),
    [
        ("CARELINE_INTERNAL_API_KEY", "internal_api_key"),
        ("CARELINE_PIN_HMAC_SECRET", "pin_hmac_secret"),
        ("CARELINE_DOCTOR_CREDENTIALS", "doctor_credentials"),
    ],
)
def test_public_demo_rejects_each_dev_default(public_env, missing, match):
    _set_strong(public_env, **{missing: None})
    with pytest.raises(ValueError, match=match):
        Settings(_env_file=None).assert_prod_safe()


@pytest.mark.parametrize("shared", ["short", "an-otherwise-long-shared-password"])
def test_public_demo_refuses_the_shared_doctor_password(public_env, shared):
    """The shared password is a dev/demo-only fallback — set at all, it is refused."""
    _set_strong(public_env, CARELINE_DOCTOR_PASSWORD=shared)
    with pytest.raises(ValueError, match="doctor_password"):
        Settings(_env_file=None).assert_prod_safe()


def test_public_demo_refuses_shared_password_without_per_doctor_credentials(public_env):
    _set_strong(
        public_env,
        CARELINE_DOCTOR_CREDENTIALS=None,
        CARELINE_DOCTOR_PASSWORD="an-otherwise-long-shared-password",
    )
    with pytest.raises(ValueError, match="doctor_"):
        Settings(_env_file=None).assert_prod_safe()


def test_public_demo_accepts_strong_config(public_env):
    _set_strong(public_env)
    Settings(_env_file=None).assert_prod_safe()


def test_production_also_requires_per_doctor_credentials(monkeypatch):
    monkeypatch.setenv("CARELINE_ENVIRONMENT", "production")
    monkeypatch.delenv("CARELINE_DOCTOR_PASSWORD", raising=False)
    _set_strong(monkeypatch, CARELINE_DOCTOR_CREDENTIALS=None)
    with pytest.raises(ValueError, match="doctor_credentials"):
        Settings(_env_file=None).assert_prod_safe()


def test_production_refuses_the_shared_doctor_password(monkeypatch):
    monkeypatch.setenv("CARELINE_ENVIRONMENT", "production")
    _set_strong(monkeypatch, CARELINE_DOCTOR_PASSWORD="prod-shared-password-long")
    with pytest.raises(ValueError, match="doctor_password"):
        Settings(_env_file=None).assert_prod_safe()


def test_public_demo_app_refuses_to_start_with_dev_defaults(public_env):
    with pytest.raises(Exception, match="jwt_secret"):
        with TestClient(create_app()):
            pass


def test_public_demo_app_boots_with_strong_secrets_and_keeps_demo_routes(public_env):
    _set_strong(public_env)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        assert client.get("/health").status_code == 200
        ask = client.post("/demo/ask", json={"question": "Can I take paracetamol?"})
        assert ask.status_code == 200
        assert client.get("/api/meta").json()["demo"] is True


def test_render_blueprint_enables_public_demo_mode():
    blueprint = (Path(__file__).resolve().parents[3] / "render.yaml").read_text()
    assert "CARELINE_PUBLIC_DEMO" in blueprint
    block = blueprint[blueprint.index("CARELINE_PUBLIC_DEMO") :]
    assert 'value: "true"' in block.splitlines()[1]
    # Per-doctor credentials, never the shared dev-fallback password (SECURITY-2).
    assert "CARELINE_DOCTOR_CREDENTIALS" in blueprint
    assert "key: CARELINE_DOCTOR_PASSWORD" not in blueprint
    # The env var Settings actually reads is CARELINE_ENVIRONMENT.
    assert "key: CARELINE_ENV\n" not in blueprint
