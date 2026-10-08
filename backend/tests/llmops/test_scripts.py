"""Ops scripts — shadow reproducibility, honest cost units, meaningful load runs."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import careline.domain.brain.brain as brain_module
import careline.domain.gates.chain as chain_module
from scripts import cost_report, load_test, shadow_compare

# -- shadow_compare -------------------------------------------------------------


def test_policy_versions_discovered():
    assert {"v1", "v2", "v3"} <= set(shadow_compare.available_versions())


def test_v1_arm_is_rebuilt_from_the_v1_artifact_and_reproduces_baseline_v0():
    rail = shadow_compare.build_rail("v1")
    assert "literal patterns only" in rail.fidelity
    # v1 had no semantic layer: a paraphrased emergency slips through.
    assert rail.check_red_flag("there is a tightness in my chest and my left arm feels funny") is None
    assert rail.check_red_flag("I have chest pain") is not None
    metrics = shadow_compare.run_variant(rail)
    # The committed baseline-v0 number (evals/reports/baseline-v0.json).
    assert metrics["missed_emergencies"] == 58
    assert metrics["policy"].startswith("red_flags@v1+")


def test_rails_are_restored_after_a_shadow_run():
    before = (brain_module.check_red_flag, chain_module.check_red_flag,
              chain_module.check_acute_concern)
    shadow_compare.run_variant(shadow_compare.build_rail("v1"))
    after = (brain_module.check_red_flag, chain_module.check_red_flag,
             chain_module.check_acute_concern)
    assert before == after


def test_active_version_uses_the_live_rails():
    rail = shadow_compare.build_rail(shadow_compare.active_version())
    assert rail.check_red_flag is None and "exact" in rail.fidelity


def test_non_active_context_version_is_labelled_approximate(monkeypatch):
    monkeypatch.setattr(shadow_compare, "active_version", lambda: "v99")
    rail = shadow_compare.build_rail("v3")
    assert rail.fidelity.startswith("APPROXIMATE")


def test_unknown_version_fails_loudly():
    with pytest.raises(SystemExit):
        shadow_compare.build_rail("v0")


def test_report_states_reproduction_scope():
    a = shadow_compare.run_variant(shadow_compare.build_rail("v1"))
    table = shadow_compare.render(a, a, "A", "B")
    assert "Reproduction scope" in table and shadow_compare.BASELINE_V0_COMMIT in table


# -- cost_report -------------------------------------------------------------------


def test_per_request_is_composed_from_per_call():
    per_call = {"reasoner": {"cost_usd": 0.0002}, "verifier": {"cost_usd": 0.0001},
                "judge": {"cost_usd": 0.0001}}
    req = cost_report.compose_request(per_call, 0.2)
    assert req["red_flag_turn_usd"] == 0.0
    assert req["declined_turn_usd"] == 0.0002
    assert req["answer_turn_usd"] == pytest.approx(0.0003)
    assert req["answer_turn_with_judge_usd"] == pytest.approx(0.00032)


def test_unknown_price_propagates_as_unknown():
    req = cost_report.compose_request({"reasoner": {"cost_usd": None}}, 0.2)
    assert req["answer_turn_usd"] is None


def test_measured_jsonl_is_per_call_by_agent(tmp_path):
    log = tmp_path / "usage.jsonl"
    rows = [
        {"agent": "reasoner", "model": "gpt-4o-mini", "input_tokens": 800, "output_tokens": 100,
         "cost_usd": 0.0002, "success": True},
        {"agent": "verifier", "model": "gpt-4o-mini", "input_tokens": 400, "output_tokens": 50,
         "cost_usd": 0.0001, "success": True},
        {"agent": "reasoner", "model": "gpt-4o-mini", "input_tokens": 0, "output_tokens": 0,
         "cost_usd": None, "success": False},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    m = cost_report.measured_from_jsonl(str(log))
    assert m["failed_calls"] == 1
    assert m["per_call"]["reasoner"]["calls"] == 1
    assert "DERIVED" in m["basis"]


def test_report_separates_units_and_labels_estimates():
    est = cost_report.estimate("gpt-4o-mini", "gpt-4o-mini", 0.2)
    text = cost_report.render(est, None, 0.2)
    assert "per CALL" in text and "per REQUEST" in text
    assert "ESTIMATE" in text
    assert set(est["per_call"]) == {"reasoner", "verifier", "judge"}


# -- load_test -----------------------------------------------------------------------


class _OK(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - stdlib name
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _OK)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_run_honours_both_minimums_and_reports_conditions(server):
    r = load_test.run(server, total=30, duration_s=0.3, concurrency=3)
    assert r["requests"] >= 30
    assert r["wall_seconds"] >= 0.3
    assert r["concurrency"] == 3 and r["errors"] == 0
    assert r["target_is_remote"] is False
    text = load_test.render(r, load_test.host_info())
    assert "concurrency 3" in text and "logical CPUs" in text


def test_defaults_are_meaningful():
    import inspect

    src = inspect.getsource(load_test.main)
    assert "default=2000" in src and "default=20.0" in src


def test_remote_target_and_headers():
    assert load_test._parse_headers(["Authorization: Bearer x"]) == {"Authorization": "Bearer x"}
    with pytest.raises(SystemExit):
        load_test._parse_headers(["no-colon"])
    r = load_test.run("http://127.0.0.1:9", total=1, duration_s=0.0, concurrency=1)
    assert r["errors"] == 1  # connection refused is an error, not a crash
