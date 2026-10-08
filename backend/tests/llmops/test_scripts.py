"""Ops scripts — shadow reproducibility, honest cost units, meaningful load runs."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import careline.domain.brain.triage as triage_module
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
    # Scored on the frozen 250 ids that existed at baseline-v0: the set has
    # since grown (more emergencies), so the full-set count is not comparable.
    metrics = shadow_compare.run_variant(rail, case_ids=shadow_compare.BASELINE_V0_CASE_IDS)
    # The committed baseline-v0 number (evals/reports/baseline-v0.json).
    assert metrics["n"] == 250
    assert metrics["missed_emergencies"] == 58
    assert metrics["policy"].startswith("red_flags@v1+")


def test_cli_case_ids_option_restricts_both_arms(tmp_path):
    out = tmp_path / "shadow.json"
    assert shadow_compare.main([
        "--a", "v1", "--case-ids", str(shadow_compare.BASELINE_V0_CASE_IDS),
        "--json", str(out),
    ]) == 0
    both = json.loads(out.read_text(encoding="utf-8"))
    assert both["a"]["n"] == both["b"]["n"] == 250
    assert both["a"]["missed_emergencies"] == 58


def _rail_sites():
    names = ("check_red_flag", "check_acute_concern", *shadow_compare._LATER_NETS)
    return tuple(getattr(mod, n, None) for mod in (triage_module, chain_module) for n in names)


def test_rails_are_restored_after_a_shadow_run():
    before = _rail_sites()
    shadow_compare.run_variant(shadow_compare.build_rail("v1"))
    assert _rail_sites() == before


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
    assert "Reproduction scope" in table and shadow_compare.BASELINE_V0_TAG in table


# -- tags, not SHAs: history rewrites must not break the demo commands ----


def _require_tag(ref: str) -> None:
    """Skip when the release tags are absent (a tarball, or a shallow clone
    without tags) — the tag contract is checked wherever the tags exist."""
    try:
        shadow_compare.resolve_ref(ref)
    except SystemExit:
        pytest.skip(f"release tag {ref!r} not present in this checkout")


def test_baseline_v0_is_the_annotated_tag_resolving_to_the_original_commit():
    assert shadow_compare.BASELINE_V0_TAG == "baseline-v0"
    _require_tag("baseline-v0")
    ref, note = shadow_compare.resolve_baseline_v0()
    assert ref == "baseline-v0" and note is None
    assert shadow_compare.resolve_ref("baseline-v0").startswith(
        shadow_compare.BASELINE_V0_FALLBACK_SHA
    )


def test_baseline_v0_falls_back_to_the_sha_with_a_clear_message(monkeypatch):
    monkeypatch.setattr(shadow_compare, "BASELINE_V0_TAG", "no-such-tag-xyz")
    ref, note = shadow_compare.resolve_baseline_v0()
    assert ref == shadow_compare.BASELINE_V0_FALLBACK_SHA
    assert note is not None and "no-such-tag-xyz" in note and "falling back" in note


@pytest.mark.parametrize("ref", ["baseline-v0", "release/red-flags-v4", "release/red-flags-v5"])
def test_replay_refs_accept_release_tag_names(ref):
    _require_tag(ref)
    sha = shadow_compare.resolve_ref(ref)
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


def test_unknown_replay_ref_fails_with_a_clear_message():
    with pytest.raises(SystemExit, match="not a tag or commit"):
        shadow_compare.resolve_ref("release/red-flags-v404")


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


# -- demo runner + in-app eval rerun use production thresholds --


def test_demo_runner_runs_clean_at_production_thresholds(capsys):
    from careline.domain.thresholds import DEFAULT_THRESHOLDS
    from careline.services import demo_runner

    assert demo_runner.run_demo() == 0
    out = capsys.readouterr().out
    assert "UNEXPECTED" not in out
    assert "Demo complete." in out
    assert f"risk ceiling {DEFAULT_THRESHOLDS.risk_ceiling}" in out
    assert "0.85" not in out
    assert "PRD" not in out


def test_demo_runner_accepts_clarify_or_escalate_for_a_discontinued_med():
    from careline.domain.enums import Verdict
    from careline.services import demo_runner

    allowed = {title: ok for title, _q, _e, ok in demo_runner._DEMO_SCENARIOS}
    assert allowed["Discontinued med"] == frozenset({Verdict.CLARIFY, Verdict.ESCALATE})


def test_offline_eval_rerun_uses_default_thresholds():
    import inspect

    from careline.services import eval_rerun

    assert "risk_ceiling=0.85" not in inspect.getsource(eval_rerun)
    results, _digest = eval_rerun.rerun_offline_eval()
    assert all(ok for _, _, ok in results), results
