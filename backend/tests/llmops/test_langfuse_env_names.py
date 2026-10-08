"""The tracer accepts Langfuse's own env names, not only the CARELINE_* ones.

Langfuse's docs and dashboard hand out LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
/ LANGFUSE_BASE_URL (or LANGFUSE_HOST). A deployer who pastes those must get
tracing, not a silent no-op; the CARELINE_* names still win when both are set.
"""

from __future__ import annotations

import pytest

from careline.adapters.observability import langfuse_tracer


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("CARELINE_LANGFUSE_PUBLIC_KEY", "CARELINE_LANGFUSE_SECRET_KEY",
              "CARELINE_LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
              "LANGFUSE_BASE_URL", "LANGFUSE_HOST"):
        monkeypatch.delenv(k, raising=False)
    langfuse_tracer._client_cache.clear()
    yield
    langfuse_tracer._client_cache.clear()


def test_standard_names_are_read(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-std")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-std")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.example")
    assert langfuse_tracer.langfuse_credentials() == ("pk-std", "sk-std", "https://cloud.example")


def test_langfuse_host_is_an_alias_for_base_url(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_HOST", "https://host.example")
    assert langfuse_tracer.langfuse_credentials()[2] == "https://host.example"


def test_careline_names_take_precedence(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-std")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-std")
    monkeypatch.setenv("CARELINE_LANGFUSE_PUBLIC_KEY", "pk-cl")
    monkeypatch.setenv("CARELINE_LANGFUSE_SECRET_KEY", "sk-cl")
    assert langfuse_tracer.langfuse_credentials()[:2] == ("pk-cl", "sk-cl")


def test_no_keys_means_no_client(monkeypatch):
    assert langfuse_tracer.langfuse_credentials() is None
    assert langfuse_tracer._client() is None


def test_trace_salt_guard_sees_standard_names(monkeypatch):
    from careline.config import Settings

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-std")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-std")
    monkeypatch.setenv("CARELINE_PUBLIC_DEMO", "true")
    monkeypatch.setenv("CARELINE_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("CARELINE_INTERNAL_API_KEY", "y" * 40)
    monkeypatch.setenv("CARELINE_PIN_HMAC_SECRET", "z" * 40)
    from careline.adapters.auth.hash_password import hash_password
    monkeypatch.setenv("CARELINE_DOCTOR_CREDENTIALS", f"dr-a:{hash_password('pw-long-enough', iterations=1000)}")
    monkeypatch.delenv("CARELINE_DOCTOR_PASSWORD", raising=False)
    monkeypatch.delenv("CARELINE_TRACE_SALT", raising=False)
    with pytest.raises(ValueError, match="CARELINE_TRACE_SALT"):
        Settings(_env_file=None).assert_prod_safe()
