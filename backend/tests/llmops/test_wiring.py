"""Integration wiring — the claims "QuestionService feeds the monitor", "GET
/monitoring is served", "the drift reference is built at startup" and "judge
spend lands in the turn's cost" are made true here, not just documented.

* every QuestionService turn calls ``online_monitor.record`` with the real
  wall-clock latency and that turn's usage scope (a raised pipeline is
  recorded as an error and re-raised — never swallowed into a fake verdict);
* the Langfuse turn trace gets the same real latency + per-turn usage and the
  turn's real start time;
* ``create_app`` mounts ``/monitoring`` and warms the drift reference OFF the
  request thread at startup; a failing build is cached and logged once, never
  retried per request;
* the async judge runs inside a copy of the turn's context, so its usage is
  part of that turn's per-request cost.

Owner: Naresh (scope ``services``/``api``).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from careline.adapters.llm import usage
from careline.adapters.llm.heuristic import HeuristicReasoner, HeuristicVerifier
from careline.adapters.llm.judge import JudgeVerdict, KeylessJudge
from careline.adapters.orchestration.graph import build_question_graph
from careline.api.app import create_app
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision
from careline.services import online_monitor, question_service
from careline.services.eval_gate import _load_seed
from careline.services.online_monitor import DriftReference, OnlineMonitor
from careline.services.question_service import QuestionService

PATIENTS, NOW = _load_seed()
RAVI = PATIENTS["ravi-kumar"]
_REF = DriftReference({"in_scope": 1.0}, frozenset({"what"}), 0.0, 5.0, 1)
_PASSWORD = "test-doctor-password-for-offline-suite"


def _session(call_id="c-1"):
    return CallSession(call_id=call_id, patient_id="ravi-kumar", doctor_id="dr-asha",
                       max_clarify_turns=2)


@pytest.fixture()
def monitor():
    m = OnlineMonitor(window=50, judge=KeylessJudge(), judge_sample_rate=0.0, reference=_REF)
    online_monitor.reset_monitor(m)
    yield m
    online_monitor.reset_monitor()


class _RecordingReasoner(HeuristicReasoner):
    """Keyless reasoner that also bills one fake LLM call per proposal."""

    def propose(self, *, question, context):
        usage.record(agent="reasoner", model="gpt-4o-mini",
                     usage=SimpleNamespace(input_tokens=1000, output_tokens=100), latency_ms=5.0)
        return super().propose(question=question, context=context)


# -- QuestionService -> monitor ---------------------------------------------------


@pytest.mark.parametrize("path", ["inline", "graph"])
def test_question_service_records_every_turn(monitor, path):
    reasoner, verifier = _RecordingReasoner(), HeuristicVerifier()
    svc = (
        QuestionService(reasoner=reasoner, verifier=verifier)
        if path == "inline"
        else QuestionService(graph=build_question_graph(reasoner=reasoner, verifier=verifier))
    )
    svc.run_question(question="What is my paracetamol dose?", patient=RAVI,
                     session=_session(), now=NOW)
    svc.run_question(question="I have crushing chest pain right now", patient=RAVI,
                     session=_session("c-2"), now=NOW)
    snap = monitor.snapshot()
    assert snap["operational"]["requests_total"] == 2
    assert snap["operational"]["latency_ms_p99"] > 0.0  # real wall time, not 0.0
    assert snap["output"]["verdict_counts"]["escalate"] >= 1
    # Per-turn usage: only the non-red-flag turn reached the (billing) reasoner.
    assert snap["cost"]["requests_with_llm_calls"] == 1
    assert snap["cost"]["total_tokens"] == 1100


def test_pipeline_error_is_recorded_and_reraised(monitor):
    class _Boom:
        def run_question(self, **_kw):
            raise RuntimeError("graph exploded")

    svc = QuestionService(graph=_Boom())
    with pytest.raises(RuntimeError):
        svc.run_question(question="q", patient=RAVI, session=_session(), now=NOW)
    snap = monitor.snapshot()
    assert snap["operational"]["errors"] == 1 and snap["operational"]["requests_total"] == 1


def test_langfuse_turn_gets_real_latency_usage_and_start(monitor, monkeypatch):
    sent = []
    monkeypatch.setattr(question_service, "record_turn", lambda **kw: sent.append(kw) or True)
    svc = QuestionService(reasoner=_RecordingReasoner(), verifier=HeuristicVerifier())
    before = datetime.now(timezone.utc)
    svc.run_question(question="What is my paracetamol dose?", patient=RAVI,
                     session=_session(), now=NOW)
    kw = sent[0]
    assert kw["latency_ms"] > 0.0
    assert kw["turn_usage"] is not None and kw["turn_usage"].calls == 1
    assert kw["trace"].start_time >= before
    assert kw["patient_id"] == "ravi-kumar"


def test_monitoring_never_breaks_the_clinical_call(monkeypatch):
    class _Broken:
        def record(self, **_kw):
            raise RuntimeError("monitor down")

    online_monitor.reset_monitor(_Broken())
    try:
        svc = QuestionService(reasoner=HeuristicReasoner(), verifier=HeuristicVerifier())
        d = svc.run_question(question="I have crushing chest pain right now", patient=RAVI,
                             session=_session(), now=NOW)
        assert d.verdict.value == "escalate"
    finally:
        online_monitor._default = None


# -- async judge spend lands in the turn's cost -------------------------------------


def test_judge_usage_is_attributed_to_the_turn_that_sampled_it():
    class _BillingJudge:
        stamp = "billing-judge"

        def judge(self, *, answer, facts):
            usage.record(agent="judge", model="gpt-4o-mini",
                         usage=SimpleNamespace(input_tokens=500, output_tokens=50),
                         latency_ms=1.0)
            return JudgeVerdict(True, 1.0, (), self.stamp)

    m = OnlineMonitor(window=10, judge=_BillingJudge(), judge_sample_rate=1.0, reference=_REF)
    try:
        answer = Decision.answer("Paracetamol 500mg twice daily for post-op pain.",
                                 confidence=0.9, risk=0.1, citations=["ravi-med-1"])
        with usage.turn_scope() as turn:
            m.record(decision=answer, question="what dose", latency_ms=1.0, cost=turn,
                     patient=RAVI, now=NOW)
        assert m.flush(5.0)
        assert "judge" in turn.agents
        cost = m.snapshot()["cost"]
        assert cost["total_tokens"] == 550
        assert cost["total_cost_usd"] == pytest.approx(
            usage.estimate_cost_usd("gpt-4o-mini", 500, 50)
        )
    finally:
        m.close()


def test_judge_on_another_turn_does_not_bleed_into_this_one():
    class _BillingJudge:
        stamp = "b"

        def judge(self, *, answer, facts):
            usage.record(agent="judge", model="gpt-4o-mini",
                         usage=SimpleNamespace(input_tokens=500, output_tokens=50),
                         latency_ms=1.0)
            return JudgeVerdict(True, 1.0, (), "b")

    m = OnlineMonitor(window=10, judge=_BillingJudge(), judge_sample_rate=1.0, reference=_REF)
    try:
        answer = Decision.answer("Paracetamol 500mg twice daily for post-op pain.",
                                 confidence=0.9, risk=0.1, citations=["ravi-med-1"])
        with usage.turn_scope() as judged_turn:
            m.record(decision=answer, question="q", latency_ms=1.0, cost=judged_turn,
                     patient=RAVI, now=NOW)
        with usage.turn_scope() as other:
            pass
        m.flush(5.0)
        assert other.calls == 0
    finally:
        m.close()


# -- drift reference: off the request thread, failure cached ----------------------


def test_reference_is_never_built_on_the_recording_thread(monkeypatch):
    calls = []
    monkeypatch.setattr(online_monitor, "build_reference",
                        lambda: calls.append(threading.current_thread().name) or _REF)
    m = OnlineMonitor(window=10, judge=KeylessJudge(), judge_sample_rate=0.0)
    try:
        m.record(decision=Decision.escalate("x"), question="q", latency_ms=1.0)
        assert calls == []  # recording never builds it inline
        assert m.snapshot()["drift"]["status"] == "reference_not_ready"
        assert m.warm_reference(background=True).join(5.0) is None
        assert calls and calls[0] != threading.current_thread().name
        assert m.snapshot()["drift"]["status"] in ("insufficient_data", "ok", "drift")
    finally:
        m.close()


def test_reference_failure_is_cached_and_logged_once(monkeypatch, caplog):
    calls = []

    def _boom():
        calls.append(1)
        raise RuntimeError("eval set unreadable")

    monkeypatch.setattr(online_monitor, "build_reference", _boom)
    m = OnlineMonitor(window=10, judge=KeylessJudge(), judge_sample_rate=0.0)
    try:
        with caplog.at_level(logging.WARNING, logger="careline.services.online_monitor"):
            m.warm_reference()
            m.warm_reference()
            for _ in range(5):
                m.record(decision=Decision.escalate("x"), question="q", latency_ms=1.0)
                m.snapshot()
        assert len(calls) == 1  # no retry storm
        assert sum("drift reference" in r.message for r in caplog.records) == 1
        drift = m.snapshot()["drift"]
        assert drift["status"] == "reference_unavailable" and "unreadable" in drift["error"]
    finally:
        m.close()


# -- app wiring --------------------------------------------------------------------


@pytest.fixture()
def app_client(monkeypatch):
    monkeypatch.setenv("CARELINE_DOCTOR_PASSWORD", _PASSWORD)
    warmed = []
    monkeypatch.setattr(OnlineMonitor, "warm_reference",
                        lambda self, background=False: warmed.append(background))
    online_monitor.reset_monitor(OnlineMonitor(window=10, judge_sample_rate=0.0, reference=_REF))
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        client.warmed = warmed
        yield client
    online_monitor.reset_monitor()


def test_app_mounts_monitoring_route(app_client):
    # Mounted by create_app itself: unauthenticated is 401 (exists), not 404.
    assert app_client.get("/monitoring").status_code == 401
    r = app_client.post("/auth/token", json={"doctor_id": "dr-A", "password": _PASSWORD})
    assert r.status_code == 200, r.text
    ok = app_client.get("/monitoring",
                        headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert ok.status_code == 200 and "drift" in ok.json()


def test_app_startup_warms_the_drift_reference_off_thread(app_client):
    assert app_client.warmed == [True]
