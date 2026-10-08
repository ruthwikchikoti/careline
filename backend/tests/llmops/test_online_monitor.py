"""Online monitor — operational, output, quality (online eval), drift, cost.

The monitor is fed one ``record(...)`` per QuestionService turn. Pinned:

* (a) operational: latency p50/p95/p99, error + fail-closed rate, throughput;
* (b) output: verdict mix, escalation rate, low-risk-escalation proxy;
* (c) quality: N% of ANSWER turns judged asynchronously (keyless judge twin
  offline), faithfulness rate;
* (d) input drift: PSI over scope mix, OOV rate vs eval vocab, length shift;
* (e) cost: tokens and $ per request from the usage turn scope;
* bounded memory, PHI-safe (no raw question text anywhere in the monitor),
  and it never raises into the clinical path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from careline.adapters.llm import usage
from careline.adapters.llm.judge import JudgeVerdict, KeylessJudge
from careline.domain.enums import ScopeCategory, TraceStatus, Verdict
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.services import online_monitor
from careline.services.online_monitor import DriftReference, OnlineMonitor

from careline.services.eval_gate import _load_seed

PATIENTS, NOW = _load_seed()
RAVI = PATIENTS["ravi-kumar"]

SECRET_QUESTION = "zebra-quokka what is my paracetamol dose"


def _answer(text="Paracetamol 500mg twice daily for post-op pain.", cites=("ravi-med-1",)):
    return Decision.answer(text, confidence=0.9, risk=0.1, citations=list(cites))


def _escalate(scope=ScopeCategory.RED_FLAG, risk=1.0, trace=None):
    return Decision.escalate("Transferring", scope=scope, risk=risk, trace=trace or ReasoningTrace())


def _fail_closed():
    trace = ReasoningTrace()
    trace.record("reasoner", TraceStatus.TERMINAL, detail="reasoner unavailable — fail closed")
    return Decision.escalate("Unable to process", trace=trace)


@pytest.fixture()
def reference():
    return DriftReference(
        scope_mix={"in_scope": 0.5, "out_of_scope": 0.3, "red_flag": 0.2},
        vocab=frozenset("what is my paracetamol dose how often take the diet".split()),
        oov_rate=0.10,
        mean_tokens=6.0,
        n=100,
    )


@pytest.fixture()
def mon(reference):
    m = OnlineMonitor(window=50, judge=KeylessJudge(), judge_sample_rate=1.0,
                      reference=reference, seed=7)
    yield m
    m.close()


# -- (a) operational -----------------------------------------------------------


def test_latency_percentiles_and_throughput(mon):
    for ms in range(1, 101):
        mon.record(decision=_escalate(), question="q", latency_ms=float(ms))
    op = mon.snapshot()["operational"]
    assert op["requests_total"] == 100
    assert op["window_size"] == 50  # ring buffer: last 50 only (51..100)
    assert op["latency_ms_p50"] == pytest.approx(76.0, abs=1)
    assert op["latency_ms_p95"] == pytest.approx(98.0, abs=1)
    assert op["latency_ms_p99"] == pytest.approx(100.0, abs=1)
    assert op["throughput_rpm_1m"] > 0


def test_fail_closed_and_error_rates(mon):
    mon.record(decision=_fail_closed(), question="q", latency_ms=5.0)
    mon.record(decision=_answer(), question="q", latency_ms=5.0, patient=RAVI, now=NOW)
    mon.record_error(latency_ms=3.0)
    mon.record(decision=_escalate(), question="q", latency_ms=5.0)
    op = mon.snapshot()["operational"]
    assert op["fail_closed"] == 1
    assert op["errors"] == 1
    assert op["fail_closed_rate"] == pytest.approx(1 / 4)
    assert op["error_rate"] == pytest.approx(1 / 4)


# -- (b) output -----------------------------------------------------------------


def test_verdict_mix_and_escalation_rate(mon):
    for _ in range(3):
        mon.record(decision=_answer(), question="q", latency_ms=1.0, patient=RAVI, now=NOW)
    mon.record(decision=Decision.clarify("which one?"), question="q", latency_ms=1.0)
    mon.record(decision=_escalate(), question="q", latency_ms=1.0)
    mon.record(decision=_escalate(scope=ScopeCategory.OUT_OF_SCOPE, risk=0.1), question="q",
               latency_ms=1.0)
    out = mon.snapshot()["output"]
    assert out["verdict_counts"] == {"answer": 3, "clarify": 1, "escalate": 2}
    assert out["escalation_rate"] == pytest.approx(2 / 6)
    # Only the low-risk, no-safety-signal escalation counts toward the proxy.
    assert out["low_risk_escalation_rate"] == pytest.approx(1 / 6)
    assert out["scope_counts"]["red_flag"] == 1


# -- (c) quality / online eval --------------------------------------------------


def test_answers_are_judged_asynchronously(mon):
    mon.record(decision=_answer(), question="q", latency_ms=1.0, patient=RAVI, now=NOW)
    mon.record(
        decision=_answer("Paracetamol 500mg twice daily. Also take ibuprofen 800mg tonight."),
        question="q", latency_ms=1.0, patient=RAVI, now=NOW,
    )
    mon.record(decision=_escalate(), question="q", latency_ms=1.0)  # never judged
    assert mon.flush(timeout=5.0)
    q = mon.snapshot()["quality"]
    assert q["sampled"] == 2 and q["judged"] == 2
    assert q["faithful"] == 1
    assert q["faithfulness_rate"] == pytest.approx(0.5)
    assert q["judge"] == "judge-keyless@v1"
    assert q["sample_rate"] == 1.0


def test_sample_rate_zero_judges_nothing(reference):
    m = OnlineMonitor(window=10, judge=KeylessJudge(), judge_sample_rate=0.0, reference=reference)
    m.record(decision=_answer(), question="q", latency_ms=1.0, patient=RAVI, now=NOW)
    m.flush(1.0)
    assert m.snapshot()["quality"]["sampled"] == 0
    m.close()


def test_sample_rate_from_env(monkeypatch, reference):
    monkeypatch.setenv("CARELINE_JUDGE_SAMPLE_RATE", "0.35")
    m = OnlineMonitor(reference=reference, judge=KeylessJudge())
    assert m.snapshot()["quality"]["sample_rate"] == 0.35
    m.close()
    monkeypatch.delenv("CARELINE_JUDGE_SAMPLE_RATE")
    m = OnlineMonitor(reference=reference, judge=KeylessJudge())
    assert m.snapshot()["quality"]["sample_rate"] == 0.2  # default
    m.close()


def test_judge_errors_are_counted_not_scored_faithful(reference):
    class _Broken:
        stamp = "broken"

        def judge(self, **_kw):
            raise RuntimeError("provider down")

    m = OnlineMonitor(window=10, judge=_Broken(), judge_sample_rate=1.0, reference=reference)
    m.record(decision=_answer(), question="q", latency_ms=1.0, patient=RAVI, now=NOW)
    m.flush(5.0)
    q = m.snapshot()["quality"]
    assert q["judge_errors"] == 1 and q["faithful"] == 0
    assert q["faithfulness_rate"] is None  # nothing successfully judged
    m.close()


def test_citation_not_valid_now_is_unfaithful(mon):
    mon.record(decision=_answer(cites=("meera-med-1",)), question="q", latency_ms=1.0,
               patient=RAVI, now=NOW)  # another patient's fact id
    mon.flush(5.0)
    q = mon.snapshot()["quality"]
    assert q["judged"] == 1 and q["faithful"] == 0


def test_low_faithfulness_raises_an_alert(reference):
    class _Unfaithful:
        stamp = "u"

        def judge(self, **_kw):
            return JudgeVerdict(False, 0.1, ("x",), "u")

    m = OnlineMonitor(window=50, judge=_Unfaithful(), judge_sample_rate=1.0, reference=reference)
    for _ in range(12):
        m.record(decision=_answer(), question="q", latency_ms=1.0, patient=RAVI, now=NOW)
    m.flush(5.0)
    assert any("faithfulness" in a for a in m.snapshot()["alerts"])
    m.close()


# -- (d) input drift --------------------------------------------------------------


def test_no_drift_on_in_distribution_traffic(mon):
    for i in range(40):
        scope = ["in_scope"] * 5 + ["out_of_scope"] * 3 + ["red_flag"] * 2
        s = ScopeCategory(scope[i % 10])
        d = _answer() if s is ScopeCategory.IN_SCOPE else _escalate(scope=s)
        mon.record(decision=d, question="what is my paracetamol dose", latency_ms=1.0,
                   patient=RAVI, now=NOW)
    drift = mon.snapshot()["drift"]
    assert drift["status"] == "ok"
    assert drift["scope_psi"] < 0.1
    assert drift["drifted"] is False


def test_drift_flagged_on_shifted_traffic(mon):
    for _ in range(40):
        mon.record(
            decision=_escalate(scope=ScopeCategory.OUT_OF_SCOPE, risk=0.1),
            question="cricket score yesterday tournament final innings batsman wicket",
            latency_ms=1.0,
        )
    drift = mon.snapshot()["drift"]
    assert drift["drifted"] is True
    assert drift["scope_psi"] > 0.2
    assert drift["oov_rate"] > 0.9
    assert any("drift" in a for a in mon.snapshot()["alerts"])


def test_drift_needs_minimum_samples(mon):
    mon.record(decision=_escalate(), question="cricket", latency_ms=1.0)
    assert mon.snapshot()["drift"]["status"] == "insufficient_data"


def test_reference_is_built_from_the_eval_set():
    ref = online_monitor.build_reference()
    assert ref.n >= 250
    assert abs(sum(ref.scope_mix.values()) - 1.0) < 1e-6
    assert "paracetamol" in ref.vocab
    assert 0.0 < ref.oov_rate < 1.0  # held-out vs dev vocabulary, a real baseline
    assert ref.mean_tokens > 3


# -- (e) cost -----------------------------------------------------------------------


def test_cost_per_request_from_turn_usage(mon):
    usage.reset()
    with usage.turn_scope() as t1:
        usage.record(agent="reasoner", model="gpt-4o-mini",
                     usage=SimpleNamespace(input_tokens=1000, output_tokens=100), latency_ms=1.0)
        usage.record(agent="verifier", model="gpt-4o-mini",
                     usage=SimpleNamespace(input_tokens=1000, output_tokens=100), latency_ms=1.0)
    mon.record(decision=_answer(), question="q", latency_ms=1.0, cost=t1, patient=RAVI, now=NOW)
    with usage.turn_scope() as t2:
        pass  # a red-flag turn: no LLM call
    mon.record(decision=_escalate(), question="q", latency_ms=1.0, cost=t2)
    cost = mon.snapshot()["cost"]
    expected = usage.estimate_cost_usd("gpt-4o-mini", 2000, 200)
    assert cost["requests_with_llm_calls"] == 1
    assert cost["total_cost_usd"] == pytest.approx(expected)
    assert cost["mean_cost_usd_per_request"] == pytest.approx(expected / 2)
    assert cost["mean_tokens_per_request"] == pytest.approx(2200 / 2)
    assert cost["basis"].startswith("estimate")


def test_plain_float_cost_is_accepted(mon):
    mon.record(decision=_escalate(), question="q", latency_ms=1.0, cost=0.002)
    assert mon.snapshot()["cost"]["total_cost_usd"] == pytest.approx(0.002)


# -- PHI, bounds, robustness ------------------------------------------------------------


def test_no_raw_question_text_is_retained(mon):
    mon.record(decision=_answer(), question=SECRET_QUESTION, latency_ms=1.0, patient=RAVI,
               now=NOW)
    mon.flush(5.0)
    blob = json.dumps(mon.snapshot()) + repr(vars(mon))
    assert "zebra" not in blob and "quokka" not in blob
    assert "Paracetamol 500mg twice" not in json.dumps(mon.snapshot())


def test_record_never_raises(mon):
    mon.record(decision=None, question=None, latency_ms="nan?")  # garbage in
    mon.record(decision=_escalate(), question="q", latency_ms=1.0)
    assert mon.snapshot()["operational"]["requests_total"] >= 1


def test_judge_queue_is_bounded(reference):
    import threading

    gate = threading.Event()

    class _Slow:
        stamp = "slow"

        def judge(self, **_kw):
            gate.wait(5.0)
            return JudgeVerdict(True, 1.0, (), "slow")

    m = OnlineMonitor(window=10, judge=_Slow(), judge_sample_rate=1.0, reference=reference,
                      judge_queue_size=2)
    for _ in range(10):
        m.record(decision=_answer(), question="q", latency_ms=1.0, patient=RAVI, now=NOW)
    gate.set()
    m.flush(5.0)
    q = m.snapshot()["quality"]
    assert q["dropped"] > 0
    assert q["sampled"] == q["judged"] + q["dropped"] + q["judge_errors"] + q["pending"]
    m.close()


def test_module_level_record_and_snapshot(monkeypatch, reference):
    online_monitor.reset_monitor(OnlineMonitor(window=5, judge=KeylessJudge(),
                                               judge_sample_rate=0.0, reference=reference))
    online_monitor.record(decision=_escalate(), question="q", latency_ms=2.0)
    snap = online_monitor.snapshot()
    assert snap["operational"]["requests_total"] == 1
    assert set(snap) >= {"operational", "output", "quality", "drift", "cost", "alerts"}
    online_monitor.reset_monitor()
