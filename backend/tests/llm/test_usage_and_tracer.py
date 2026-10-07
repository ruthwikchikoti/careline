"""Usage/cost capture and the no-key Langfuse no-op."""

from __future__ import annotations

from types import SimpleNamespace

from careline.adapters.llm import usage
from careline.adapters.observability import record_turn
from careline.adapters.observability.langfuse_tracer import _patient_hash


class _OpenAIStyle(SimpleNamespace):
    pass


def test_estimate_cost_known_and_unknown_models():
    assert usage.estimate_cost_usd("gpt-4o-mini", 1_000_000, 1_000_000) == 0.75
    assert usage.estimate_cost_usd("gpt-4o-mini", 0, 0) == 0.0
    assert usage.estimate_cost_usd("mystery-model", 100, 100) is None


def test_record_accepts_provider_usage_shapes():
    usage.reset()
    openai_style = _OpenAIStyle(prompt_tokens=100, completion_tokens=50)
    anthropic_style = _OpenAIStyle(input_tokens=200, output_tokens=100)
    assert usage.record(agent="reasoner", model="gpt-4o-mini", usage=openai_style, latency_ms=120.0)
    assert usage.record(agent="verifier", model="gpt-4o-mini", usage=anthropic_style, latency_ms=80.0)
    assert usage.record(agent="reasoner", model="unknown", usage=anthropic_style, latency_ms=1.0)
    recs = usage.records()
    assert len(recs) == 3
    assert recs[0].input_tokens == 100 and recs[0].output_tokens == 50
    assert recs[1].input_tokens == 200
    assert recs[0].artifacts["red_flags"].startswith("red_flags@")  # version-stamped
    s = usage.summary()
    assert s["calls"] == 3
    assert s["calls_with_unknown_price"] == 1
    assert s["by_agent"] == {"reasoner": 2, "verifier": 1}
    assert s["per_call_usd"] is not None


def test_record_never_raises_on_garbage():
    usage.reset()
    assert usage.record(agent="x", model="y", usage="not-a-usage-object", latency_ms=0) is None or True
    # a None usage records zero tokens rather than failing the clinical call
    assert usage.record(agent="x", model="gpt-4o-mini", usage=None, latency_ms=5.0)
    assert usage.records()[-1].total_tokens == 0


def test_failed_calls_do_not_pollute_cost_or_latency():
    usage.reset()
    good = _OpenAIStyle(prompt_tokens=100, completion_tokens=50)
    usage.record(agent="reasoner", model="gpt-4o-mini", usage=good, latency_ms=100.0)
    usage.record(
        agent="reasoner", model="gpt-4o-mini", usage=None,
        latency_ms=60000.0, success=False,
    )  # a 60 s timeout must not become the reported p50/p99
    s = usage.summary()
    assert s["calls"] == 2 and s["failed_calls"] == 1
    assert s["latency_ms_p50"] == 100.0  # successful calls only
    assert s["per_call_usd"] == usage.estimate_cost_usd("gpt-4o-mini", 100, 50)


def test_per_call_usd_denominator_is_known_price_calls_only():
    usage.reset()
    good = _OpenAIStyle(prompt_tokens=100, completion_tokens=50)
    unknown = _OpenAIStyle(input_tokens=999, output_tokens=999)
    usage.record(agent="r", model="gpt-4o-mini", usage=good, latency_ms=1.0)
    usage.record(agent="r", model="mystery-model", usage=unknown, latency_ms=1.0)
    s = usage.summary()
    assert s["per_call_usd"] == usage.estimate_cost_usd("gpt-4o-mini", 100, 50)


def test_tracer_is_a_noop_without_keys(monkeypatch):
    monkeypatch.delenv("CARELINE_LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("CARELINE_LANGFUSE_SECRET_KEY", raising=False)
    assert record_turn(
        question="q", patient_id="p", model="m", verdict="answer", scope="in_scope", latency_ms=1.0
    ) is False  # silently skipped — and never raised


def test_patient_hash_is_stable_and_salted(monkeypatch):
    monkeypatch.setenv("CARELINE_TRACE_SALT", "s1")
    a = _patient_hash("ravi-kumar")
    assert a == _patient_hash("ravi-kumar")
    assert a != "ravi-kumar" and len(a) == 12
    monkeypatch.setenv("CARELINE_TRACE_SALT", "s2")
    assert _patient_hash("ravi-kumar") != a
