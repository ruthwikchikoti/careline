"""Defensive env parsing + the judge prompt in the public registry API.

A typo in an ops knob (``CARELINE_MONITOR_WINDOW=1k``) must never take the
clinical service down at import/startup: a bad value falls back to the
default and logs one warning. And the judge prompt is a registered release
artifact like the others, so ``active_versions()`` — which every report and
trace stamps — includes ``judge@v1`` via the public loader.

Owner: Naresh (scope ``services``) / Srujan (scope ``llm``).
"""

from __future__ import annotations

import logging

import pytest

from careline.adapters.llm import judge, prompt_registry, usage
from careline.adapters.llm.judge import KeylessJudge
from careline.services.online_monitor import DriftReference, OnlineMonitor

_REF = DriftReference({"in_scope": 1.0}, frozenset(), 0.0, 1.0, 1)


@pytest.mark.parametrize("raw", ["abc", "", "-5", "0", "1.5e", "nan"])
def test_usage_buffer_env_bad_value_falls_back(monkeypatch, caplog, raw):
    monkeypatch.setenv("CARELINE_USAGE_BUFFER", raw)
    with caplog.at_level(logging.WARNING):
        assert usage._capacity_from_env() == usage.DEFAULT_CAPACITY
    if raw:
        assert any("CARELINE_USAGE_BUFFER" in r.message for r in caplog.records)


def test_usage_buffer_env_good_value(monkeypatch):
    monkeypatch.setenv("CARELINE_USAGE_BUFFER", "250")
    assert usage._capacity_from_env() == 250


@pytest.mark.parametrize("raw", ["abc", "-1", "0", "ten"])
def test_monitor_window_env_bad_value_falls_back(monkeypatch, caplog, raw):
    monkeypatch.setenv("CARELINE_MONITOR_WINDOW", raw)
    with caplog.at_level(logging.WARNING):
        m = OnlineMonitor(judge=KeylessJudge(), reference=_REF)
    try:
        assert m.snapshot()["operational"]["window_capacity"] == 1000
        assert any("CARELINE_MONITOR_WINDOW" in r.message for r in caplog.records)
    finally:
        m.close()


@pytest.mark.parametrize("raw", ["abc", "nan", "inf", "-0.5", "1.7"])
def test_judge_sample_rate_env_bad_value_falls_back(monkeypatch, caplog, raw):
    monkeypatch.setenv("CARELINE_JUDGE_SAMPLE_RATE", raw)
    with caplog.at_level(logging.WARNING):
        m = OnlineMonitor(judge=KeylessJudge(), reference=_REF)
    try:
        assert m.snapshot()["quality"]["sample_rate"] == 0.2
        assert any("CARELINE_JUDGE_SAMPLE_RATE" in r.message for r in caplog.records)
    finally:
        m.close()


def test_monitor_window_env_good_value(monkeypatch):
    monkeypatch.setenv("CARELINE_MONITOR_WINDOW", "64")
    m = OnlineMonitor(judge=KeylessJudge(), reference=_REF)
    try:
        assert m.snapshot()["operational"]["window_capacity"] == 64
    finally:
        m.close()


def test_active_versions_includes_the_judge_prompt():
    prompt_registry.reload()
    versions = prompt_registry.active_versions()
    assert versions["judge"].startswith("judge@v1+")
    assert versions["judge"] == prompt_registry.load_prompt("judge").stamp


def test_judge_loads_through_the_public_registry_api():
    assert judge.load_judge_prompt().stamp == prompt_registry.load_prompt("judge").stamp


def test_unregistered_prompt_still_fails_closed():
    with pytest.raises(prompt_registry.RegistryError):
        prompt_registry.load_prompt("nonexistent")
