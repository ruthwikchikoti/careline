"""Settings + to_thresholds() + prod guard tests (NR-1).

Pins offline behaviour: defaults match Thresholds, to_thresholds()
bridges correctly, assert_prod_safe() raises on unsafe prod overrides.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from careline.config import Environment, Settings, get_settings
from careline.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds


# -- T1: default parity ------------------------------------------------------


def test_settings_defaults_match_default_thresholds():
    settings = Settings()
    assert settings.confidence_floor == DEFAULT_THRESHOLDS.confidence_floor
    assert settings.risk_ceiling == DEFAULT_THRESHOLDS.risk_ceiling
    assert settings.max_clarify_turns == DEFAULT_THRESHOLDS.max_clarify_turns
    assert settings.environment is Environment.DEVELOPMENT


# -- T2: bridge round-trip ---------------------------------------------------


def test_to_thresholds_with_defaults_matches_default_thresholds():
    thresholds = Settings().to_thresholds()
    assert thresholds == DEFAULT_THRESHOLDS
    assert isinstance(thresholds, Thresholds)


# -- T3: env loading ---------------------------------------------------------


def test_to_thresholds_reads_env_overrides(monkeypatch):
    monkeypatch.setenv("CARELINE_CONFIDENCE_FLOOR", "0.85")
    settings = Settings()
    thresholds = settings.to_thresholds()
    assert thresholds.confidence_floor == 0.85
    assert thresholds.risk_ceiling == DEFAULT_THRESHOLDS.risk_ceiling
    assert thresholds.max_clarify_turns == DEFAULT_THRESHOLDS.max_clarify_turns


# -- T4–T7: production guard -------------------------------------------------


def test_assert_prod_safe_passes_with_defaults_in_production(monkeypatch):
    monkeypatch.setenv("CARELINE_ENVIRONMENT", "production")
    monkeypatch.setenv("CARELINE_JWT_SECRET", "prod-jwt-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("CARELINE_INTERNAL_API_KEY", "prod-internal-api-key-32bytes-min!!")
    monkeypatch.setenv("CARELINE_PIN_HMAC_SECRET", "prod-pin-hmac-secret-32bytes-min!!")
    settings = Settings()
    settings.assert_prod_safe()


def test_assert_prod_safe_rejects_lowered_confidence_floor_in_production(monkeypatch):
    monkeypatch.setenv("CARELINE_ENVIRONMENT", "production")
    monkeypatch.setenv("CARELINE_CONFIDENCE_FLOOR", "0.5")
    settings = Settings()
    with pytest.raises(ValueError, match="confidence_floor"):
        settings.assert_prod_safe()


def test_assert_prod_safe_rejects_raised_risk_ceiling_in_production(monkeypatch):
    monkeypatch.setenv("CARELINE_ENVIRONMENT", "production")
    monkeypatch.setenv("CARELINE_RISK_CEILING", "0.9")
    settings = Settings()
    with pytest.raises(ValueError, match="risk_ceiling"):
        settings.assert_prod_safe()


def test_assert_prod_safe_is_noop_in_development_with_unsafe_values(monkeypatch):
    monkeypatch.setenv("CARELINE_ENVIRONMENT", "development")
    monkeypatch.setenv("CARELINE_CONFIDENCE_FLOOR", "0.5")
    monkeypatch.setenv("CARELINE_RISK_CEILING", "0.9")
    settings = Settings()
    settings.assert_prod_safe()


# -- T8: is_production property ----------------------------------------------


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("development", False),
        ("staging", False),
        ("production", True),
    ],
)
def test_is_production(monkeypatch, env_value, expected):
    monkeypatch.setenv("CARELINE_ENVIRONMENT", env_value)
    settings = Settings()
    assert settings.is_production is expected


# -- T9: extra fields ignored ------------------------------------------------


def test_settings_ignores_unknown_env_fields(monkeypatch):
    monkeypatch.setenv("CARELINE_SURPRISE_FIELD", "nope")
    settings = Settings()
    assert not hasattr(settings, "surprise_field")


# -- T10: frozen Thresholds contract -----------------------------------------


def test_to_thresholds_result_is_frozen():
    thresholds = Settings().to_thresholds()
    with pytest.raises(ValidationError):
        thresholds.confidence_floor = 0.99


def test_settings_mongo_uri_defaults_to_none():
    settings = Settings()
    assert settings.mongo_uri is None


def test_settings_mongo_uri_reads_env(monkeypatch):
    monkeypatch.setenv("CARELINE_MONGO_URI", "mongodb://localhost:27017")
    settings = Settings()
    assert settings.mongo_uri == "mongodb://localhost:27017"


# -- factory -----------------------------------------------------------------


def test_get_settings_returns_settings_instance():
    settings = get_settings()
    assert isinstance(settings, Settings)
