"""Live-model clients must time out fast and fail closed (OPERATIONS: provider outage).

The OpenAI SDK default is a 600 s timeout with 2 retries: a hung provider would
hold a patient for ten minutes before the Brain could escalate. Every adapter
builds its client from one helper with a short, configurable timeout.
"""

from __future__ import annotations

import pytest

from careline.adapters.llm.openai_backend import openai_client_kwargs


def test_default_timeout_is_short_and_retries_bounded(monkeypatch):
    monkeypatch.delenv("CARELINE_LLM_TIMEOUT_S", raising=False)
    kwargs = openai_client_kwargs(api_key="k")
    assert kwargs["api_key"] == "k"
    assert kwargs["timeout"] <= 30.0
    assert kwargs["max_retries"] <= 1


def test_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("CARELINE_LLM_TIMEOUT_S", "12.5")
    assert openai_client_kwargs(api_key=None)["timeout"] == 12.5


@pytest.mark.parametrize("bad", ["abc", "-3", "0", "nan", "9999"])
def test_bad_timeout_falls_back_to_default(monkeypatch, bad):
    monkeypatch.setenv("CARELINE_LLM_TIMEOUT_S", bad)
    assert 0 < openai_client_kwargs(api_key=None)["timeout"] <= 30.0
