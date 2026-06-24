"""SR-6 — the adapter factory: default keyless wiring + production guard."""

from __future__ import annotations

import pytest

from careline.adapters.factory import (
    LLMBackend,
    LLMConfig,
    build_extractor,
    build_reasoner,
    build_verifier,
)
from careline.adapters.llm.anthropic_backend import AnthropicReasoner
from careline.adapters.llm.extraction_backend import OpenAIExtractor
from careline.adapters.llm.heuristic import HeuristicReasoner, HeuristicVerifier
from careline.services.extraction_service import HeuristicExtractor


def test_default_is_keyless_heuristic():
    assert isinstance(build_reasoner(), HeuristicReasoner)
    assert isinstance(build_verifier(), HeuristicVerifier)


def test_selects_anthropic_backend():
    config = LLMConfig(backend=LLMBackend.ANTHROPIC, api_key="sk-test")
    assert isinstance(build_reasoner(config), AnthropicReasoner)


def test_production_guard_rejects_heuristic_stub():
    config = LLMConfig(backend=LLMBackend.HEURISTIC, environment="production")
    with pytest.raises(RuntimeError, match="production"):
        build_reasoner(config)
    with pytest.raises(RuntimeError, match="production"):
        build_verifier(config)


def test_real_backend_allowed_in_production():
    config = LLMConfig(backend=LLMBackend.ANTHROPIC, environment="production", api_key="sk")
    assert isinstance(build_reasoner(config), AnthropicReasoner)


class TestBuildExtractor:
    def test_default_is_heuristic_fallback(self):
        assert isinstance(build_extractor(), HeuristicExtractor)

    def test_openai_backend_selects_llm_extractor(self):
        config = LLMConfig(backend=LLMBackend.OPENAI, api_key="sk-test")
        assert isinstance(build_extractor(config), OpenAIExtractor)

    def test_anthropic_backend_falls_back_to_heuristic_extractor(self):
        # Only an OpenAI extractor exists; other backends use the regex fallback.
        config = LLMConfig(backend=LLMBackend.ANTHROPIC, api_key="sk")
        assert isinstance(build_extractor(config), HeuristicExtractor)


class TestFromEnv:
    def test_empty_env_is_heuristic_dev(self):
        config = LLMConfig.from_env({})
        assert config.backend is LLMBackend.HEURISTIC
        assert config.environment == "dev"

    def test_reads_backend_and_env(self):
        config = LLMConfig.from_env(
            {"CARELINE_LLM_BACKEND": "anthropic", "CARELINE_ENV": "production"}
        )
        assert config.backend is LLMBackend.ANTHROPIC
        assert config.environment == "production"

    def test_unknown_backend_rejected(self):
        with pytest.raises(ValueError):
            LLMConfig.from_env({"CARELINE_LLM_BACKEND": "gpt-2-on-a-toaster"})
