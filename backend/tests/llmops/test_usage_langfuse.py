"""Usage capture + Langfuse tracer — honest per-turn numbers, bounded memory.

Findings pinned here:

* the Langfuse turn trace sent a hardcoded model ("deterministic-spine") and
  latency (0.0), and a CUMULATIVE process-wide cost as if it were the turn's —
  it must send the real model(s), the real latency and per-turn tokens/cost;
* ``langfuse`` was never a declared dependency (``obs`` extra);
* the usage buffer grew without bound;
* the extractor defaulted to an unpriced model and recorded no usage, so
  extraction spend was invisible (claims-audit-extractor-budget-model);
* claude-haiku-4-5 was priced at $0.80/$4 — published price is $1/$5 per MTok.
"""

from __future__ import annotations

import threading
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from careline.adapters.llm import usage
from careline.adapters.observability import langfuse_tracer

_U = SimpleNamespace


@pytest.fixture(autouse=True)
def _clean():
    usage.reset()
    yield
    usage.reset()


# -- usage: ring buffer -------------------------------------------------------


def test_usage_buffer_is_bounded(monkeypatch):
    usage.set_capacity(5)
    try:
        for i in range(12):
            usage.record(agent="r", model="gpt-4o-mini", usage=_U(input_tokens=i, output_tokens=0),
                         latency_ms=1.0)
        recs = usage.records()
        assert len(recs) == 5
        assert [r.input_tokens for r in recs] == [7, 8, 9, 10, 11]  # newest kept
        assert usage.summary()["calls_total"] == 12  # lifetime counter survives eviction
    finally:
        usage.set_capacity(usage.DEFAULT_CAPACITY)


def test_default_capacity_is_finite():
    assert 0 < usage.DEFAULT_CAPACITY <= 100_000


# -- usage: per-turn scope ----------------------------------------------------


def test_turn_scope_collects_only_this_turns_calls():
    usage.record(agent="r", model="gpt-4o-mini", usage=_U(input_tokens=999, output_tokens=999),
                 latency_ms=1.0)  # an earlier turn
    with usage.turn_scope() as turn:
        usage.record(agent="reasoner", model="gpt-4o-mini",
                     usage=_U(input_tokens=100, output_tokens=20), latency_ms=50.0)
        usage.record(agent="verifier", model="gpt-4o-mini",
                     usage=_U(input_tokens=200, output_tokens=10), latency_ms=30.0)
    usage.record(agent="r", model="gpt-4o-mini", usage=_U(input_tokens=5, output_tokens=5),
                 latency_ms=1.0)  # a later turn
    assert turn.calls == 2
    assert turn.input_tokens == 300 and turn.output_tokens == 30
    assert turn.total_tokens == 330
    assert turn.cost_usd == pytest.approx(usage.estimate_cost_usd("gpt-4o-mini", 300, 30))
    assert turn.models == ("gpt-4o-mini",)
    assert turn.agents == ("reasoner", "verifier")


def test_turn_scope_is_isolated_across_threads():
    seen = {}

    def worker(name, tokens):
        with usage.turn_scope() as turn:
            usage.record(agent=name, model="gpt-4o-mini",
                         usage=_U(input_tokens=tokens, output_tokens=0), latency_ms=1.0)
        seen[name] = turn.input_tokens

    threads = [threading.Thread(target=worker, args=(f"t{i}", 10 * (i + 1))) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == {"t0": 10, "t1": 20, "t2": 30, "t3": 40}


def test_turn_without_llm_calls_is_zero_cost_and_no_model():
    with usage.turn_scope() as turn:
        pass
    assert turn.calls == 0 and turn.cost_usd == 0.0 and turn.models == ()


def test_failed_calls_count_but_do_not_cost():
    with usage.turn_scope() as turn:
        usage.record(agent="reasoner", model="gpt-4o-mini", usage=None, latency_ms=60000.0,
                     success=False)
    assert turn.calls == 1 and turn.failed_calls == 1 and turn.cost_usd == 0.0


def test_unknown_price_is_not_guessed():
    with usage.turn_scope() as turn:
        usage.record(agent="reasoner", model="mystery", usage=_U(input_tokens=1, output_tokens=1),
                     latency_ms=1.0)
    assert turn.cost_usd is None and turn.unpriced_calls == 1


# -- price table ---------------------------------------------------------------


def test_haiku_45_price_is_the_published_one():
    assert usage.PRICE_TABLE_USD_PER_MTOK["claude-haiku-4-5"] == (1.00, 5.00)
    assert usage.PRICE_TABLE_AS_OF


# -- extractor: model + usage ---------------------------------------------------


def test_extractor_defaults_to_budget_model_and_records_usage():
    from careline.adapters.llm.extraction_backend import DEFAULT_MODEL, OpenAIExtractor, _ExtractionDTO

    assert DEFAULT_MODEL == "gpt-4o-mini"
    assert DEFAULT_MODEL in usage.PRICE_TABLE_USD_PER_MTOK  # priced, so spend is visible

    class _Client:
        class responses:  # noqa: N801
            @staticmethod
            def parse(**_kw):
                return SimpleNamespace(output_parsed=_ExtractionDTO(facts=[]),
                                       usage=_U(input_tokens=400, output_tokens=50))

    OpenAIExtractor(client=_Client()).extract(
        transcript="Paracetamol 500mg twice daily.", consultation_id="c1",
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    rec = usage.records()[-1]
    assert rec.agent == "extractor" and rec.model == "gpt-4o-mini"
    assert rec.input_tokens == 400 and rec.cost_usd is not None


def test_extractor_failure_is_recorded_as_failed_call():
    from careline.adapters.llm.extraction_backend import OpenAIExtractor
    from careline.domain.ports.reasoning import ReasonerUnavailable

    class _Client:
        class responses:  # noqa: N801
            @staticmethod
            def parse(**_kw):
                raise RuntimeError("boom")

    with pytest.raises(ReasonerUnavailable):
        OpenAIExtractor(client=_Client()).extract(
            transcript="x", consultation_id="c1", now=datetime(2026, 6, 1, tzinfo=timezone.utc)
        )
    rec = usage.records()[-1]
    assert rec.agent == "extractor" and rec.success is False


# -- pyproject: obs extra -----------------------------------------------------


def test_pyproject_declares_langfuse_obs_extra():
    data = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    obs = data["project"]["optional-dependencies"]["obs"]
    assert any(dep.replace(" ", "").startswith("langfuse>=2") for dep in obs)


# -- Langfuse tracer ------------------------------------------------------------


class _FakeV2:
    """Langfuse v2 low-level SDK shape: ``client.generation(**kw)``."""

    def __init__(self):
        self.generations = []

    def generation(self, **kw):
        self.generations.append(kw)
        return SimpleNamespace()


class _FakeGen:
    def __init__(self, kw):
        self.kw = kw
        self.updates = []
        self.ended = False

    def update(self, **kw):
        self.updates.append(kw)

    def end(self, **kw):
        self.ended = True


class _FakeV3:
    """Langfuse v3 SDK shape: ``client.start_generation(**kw)`` → span with ``end()``."""

    def __init__(self):
        self.gens = []

    def start_generation(self, **kw):
        g = _FakeGen(kw)
        self.gens.append(g)
        return g


def _turn(tokens=(300, 30), model="gpt-4o-mini"):
    with usage.turn_scope() as turn:
        usage.record(agent="reasoner", model=model,
                     usage=_U(input_tokens=tokens[0], output_tokens=tokens[1]), latency_ms=40.0)
    return turn


def test_v2_sends_real_model_latency_and_per_turn_cost():
    # A big earlier turn: its cost must NOT leak into this turn's trace.
    _turn(tokens=(1_000_000, 1_000_000))
    turn = _turn()
    fake = _FakeV2()
    sent = langfuse_tracer.record_turn(
        question="What is my dose?", patient_id="ravi-kumar", model="deterministic-spine",
        verdict="answer", scope="in_scope", latency_ms=123.4, turn_usage=turn, client=fake,
    )
    assert sent is True
    g = fake.generations[0]
    assert g["model"] == "gpt-4o-mini"  # real model wins over the placeholder
    assert g["metadata"]["latency_ms"] == 123.4
    assert g["usage"]["input"] == 300 and g["usage"]["output"] == 30
    assert g["usage"]["total_cost"] == pytest.approx(usage.estimate_cost_usd("gpt-4o-mini", 300, 30))
    assert g["metadata"]["cost_usd"] == pytest.approx(turn.cost_usd)
    assert "cost_usd_run_total" not in g["metadata"]
    assert (g["end_time"] - g["start_time"]).total_seconds() == pytest.approx(0.1234, abs=1e-3)
    assert "ravi-kumar" not in str(g)  # patient id only as salted hash


def test_v3_sends_generation_and_ends_it():
    turn = _turn()
    fake = _FakeV3()
    assert langfuse_tracer.record_turn(
        question="q", patient_id="p", verdict="answer", scope="in_scope",
        latency_ms=50.0, turn_usage=turn, client=fake,
    )
    g = fake.gens[0]
    assert g.kw["model"] == "gpt-4o-mini"
    assert g.kw["usage_details"] == {"input": 300, "output": 30, "total": 330}
    assert g.kw["cost_details"]["total"] == pytest.approx(turn.cost_usd)
    assert g.ended


def test_keyless_turn_reports_no_model_call_not_a_fake_model():
    with usage.turn_scope() as turn:
        pass
    fake = _FakeV2()
    langfuse_tracer.record_turn(
        question="q", patient_id="p", model="deterministic-spine", verdict="escalate",
        scope="red_flag", latency_ms=2.0, turn_usage=turn, client=fake,
    )
    g = fake.generations[0]
    assert g["model"] == "deterministic-spine"
    assert g["usage"]["input"] == 0 and g["usage"]["total_cost"] == 0.0


def test_without_turn_usage_cost_is_unknown_not_cumulative():
    _turn(tokens=(1_000_000, 1_000_000))
    fake = _FakeV2()
    langfuse_tracer.record_turn(
        question="q", patient_id="p", model="m", verdict="answer", scope="in_scope",
        latency_ms=1.0, client=fake,
    )
    g = fake.generations[0]
    assert g["metadata"]["cost_usd"] is None
    assert "total_cost" not in g.get("usage", {})


def test_tracer_never_raises_on_broken_client():
    class _Broken:
        def generation(self, **kw):
            raise RuntimeError("network down")

    assert langfuse_tracer.record_turn(
        question="q", patient_id="p", model="m", verdict="answer", scope="s",
        latency_ms=1.0, client=_Broken(),
    ) is False


def test_tracer_noop_without_keys(monkeypatch):
    monkeypatch.delenv("CARELINE_LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("CARELINE_LANGFUSE_SECRET_KEY", raising=False)
    assert langfuse_tracer.record_turn(
        question="q", patient_id="p", model="m", verdict="answer", scope="s", latency_ms=1.0
    ) is False
