"""Eval-gate regression on the INTERSECTION of case ids — growing the set is not a regression.

Growing the eval set changes every rate's denominator: v4 added hard
in-scope items, so the keyless in-scope accuracy fell 0.214 → 0.194 although
not one previously-passing case regressed. Moving the baseline in the same
push to hide that is exactly what a reviewer flagged. Instead:

* every metrics JSON carries a ``per_case`` map {case_id: {split, expected,
  verdict, correct, violations, ungrounded}};
* the regression check recomputes each enforced metric over the case ids
  present in BOTH baseline and current — same items, same denominator;
* new cases are gated only by the absolute thresholds;
* a baseline case that vanished from the eval set is a failure (deleting a
  hard case must not shrink the comparison silently);
* a baseline without ``per_case`` falls back to the aggregate comparison and
  says so on stderr.

Plus ``--case-ids FILE``: score only a frozen id list (the 250 ids that
existed at baseline-v0) so a historical number is reproducible after growth.

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import json

import pytest

from careline.services import eval_gate

_REPORTS = eval_gate._BACKEND_ROOT / "evals" / "reports"
_BASELINE_V0_IDS = _REPORTS / "baseline-v0.case_ids.txt"


def _entry(split, verdict, *, expected=None, correct=True, violations=(), ungrounded=0):
    return {
        "split": split,
        "expected": expected or {"emergency": "escalate"}.get(split, "no_answer"),
        "verdict": verdict,
        "correct": correct,
        "violations": list(violations),
        "ungrounded": ungrounded,
    }


@pytest.fixture(scope="module")
def full_results():
    patients, now = eval_gate._load_seed()
    cases = eval_gate.load_cases()
    return eval_gate.run_keyless(cases, patients, now)


# -- per_case ------------------------------------------------------------------


def test_per_case_has_every_case_and_the_required_fields(full_results):
    per_case = eval_gate.per_case_results(full_results)
    assert set(per_case) == {r.case["id"] for r in full_results}
    for entry in per_case.values():
        assert {"split", "verdict", "correct", "violations"} <= set(entry)
        assert isinstance(entry["correct"], bool)
        assert entry["correct"] == (entry["violations"] == [])


def test_per_case_rescoring_matches_score_on_the_full_set(full_results):
    # The intersection metrics are only honest if per_case re-scoring is the
    # SAME function as score() — pinned on the real full run.
    metrics = eval_gate.score(full_results)
    per_case = eval_gate.per_case_results(full_results)
    rescored = eval_gate.score_per_case(per_case, per_case.keys())
    for name in eval_gate.enforced_metrics():
        assert rescored[name] == pytest.approx(metrics[name]), name


def test_main_json_carries_per_case(tmp_path):
    out = tmp_path / "m.json"
    assert eval_gate.main(["--json", str(out)]) == 0
    metrics = json.loads(out.read_text(encoding="utf-8"))
    assert len(metrics["per_case"]) == metrics["n"]


# -- intersection regression ---------------------------------------------------


def test_new_hard_cases_do_not_read_as_a_regression():
    ins_ok = _entry("in_scope", "answer", expected="answer")
    ins_bad = _entry("in_scope", "clarify", expected="answer", correct=False,
                     violations=["expected answer, got clarify"])
    baseline = {"in_scope_answer_accuracy": 0.5,
                "per_case": {"ins-001": ins_ok, "ins-002": ins_bad}}
    # Two new, failing in-scope items: aggregate accuracy 0.5 → 0.25.
    current = {"in_scope_answer_accuracy": 0.25,
               "per_case": {"ins-001": ins_ok, "ins-002": ins_bad,
                            "ins-003": ins_bad, "ins-004": ins_bad}}
    assert eval_gate.regression_check(current, baseline) == []


def test_a_regression_on_a_shared_case_still_fails():
    ok = _entry("in_scope", "answer", expected="answer")
    bad = _entry("in_scope", "clarify", expected="answer", correct=False,
                 violations=["expected answer, got clarify"])
    baseline = {"in_scope_answer_accuracy": 1.0, "per_case": {"ins-001": ok, "ins-002": ok}}
    # Aggregate unchanged thanks to a new passing item — the shared case broke.
    current = {"in_scope_answer_accuracy": 0.667,
               "per_case": {"ins-001": ok, "ins-002": bad, "ins-003": ok, "ins-004": ok}}
    failures = eval_gate.regression_check(current, baseline)
    assert any("in_scope_answer_accuracy" in f and "shared" in f for f in failures)


def test_missed_emergency_on_a_shared_case_fails():
    hit = _entry("emergency", "escalate")
    miss = _entry("emergency", "answer", correct=False,
                  violations=["expected escalate, got answer"])
    baseline = {"missed_emergencies": 0, "per_case": {"em-001": hit}}
    current = {"missed_emergencies": 1, "per_case": {"em-001": miss}}
    assert any("missed_emergencies" in f for f in eval_gate.regression_check(current, baseline))


def test_baseline_case_deleted_from_the_eval_set_fails():
    hit = _entry("emergency", "escalate")
    baseline = {"missed_emergencies": 0, "per_case": {"em-001": hit, "em-002": hit}}
    current = {"missed_emergencies": 0, "per_case": {"em-001": hit}}
    failures = eval_gate.regression_check(current, baseline, known_ids={"em-001"})
    assert any("em-002" in f for f in failures)


def test_case_outside_this_runs_filter_but_still_in_the_set_is_not_deleted():
    hit = _entry("emergency", "escalate")
    baseline = {"missed_emergencies": 0, "per_case": {"em-001": hit, "em-002": hit}}
    current = {"missed_emergencies": 0, "per_case": {"em-001": hit}}
    assert eval_gate.regression_check(current, baseline, known_ids={"em-001", "em-002"}) == []


def test_no_shared_cases_fails_closed():
    hit = _entry("emergency", "escalate")
    baseline = {"missed_emergencies": 0, "per_case": {"em-001": hit}}
    current = {"missed_emergencies": 0, "per_case": {"em-999": hit}}
    assert eval_gate.regression_check(current, baseline)


def test_metric_missing_from_current_still_fails_in_intersection_mode():
    hit = _entry("emergency", "escalate")
    baseline = {"missed_emergencies": 0, "ungrounded_answers": 0, "per_case": {"em-001": hit}}
    current = {"missed_emergencies": 0, "per_case": {"em-001": hit}}
    failures = eval_gate.regression_check(current, baseline)
    assert any("ungrounded_answers" in f and "missing" in f for f in failures)


def test_baseline_without_per_case_falls_back_with_a_warning(capsys):
    failures = eval_gate.regression_check(
        {"in_scope_answer_accuracy": 0.19, "per_case": {}}, {"in_scope_answer_accuracy": 0.21}
    )
    assert any("in_scope_answer_accuracy" in f for f in failures)
    assert "per_case" in capsys.readouterr().err


# -- committed baselines -------------------------------------------------------


def test_v3_baseline_carries_per_case_for_its_250_cases():
    v3 = json.loads((_REPORTS / "after-policy-v3.json").read_text(encoding="utf-8"))
    assert len(v3["per_case"]) == v3["n"] == 250
    # The per_case section reproduces v3's own committed aggregates.
    rescored = eval_gate.score_per_case(v3["per_case"], v3["per_case"].keys())
    for name in eval_gate.enforced_metrics():
        assert rescored[name] == pytest.approx(eval_gate._baseline_value(v3, name)), name


def test_current_set_passes_against_v3_on_the_intersection(capsys):
    path = _REPORTS / "after-policy-v3.json"
    assert eval_gate.main(["--baseline", str(path)]) == 0
    assert "250 shared cases" in capsys.readouterr().out


# -- frozen case-id lists ------------------------------------------------------


def test_baseline_v0_case_id_list_is_the_original_250():
    ids = eval_gate.load_case_ids(_BASELINE_V0_IDS)
    assert len(ids) == 250
    assert {i for i in ids if i.startswith("em-")} == {f"em-{n:03d}" for n in range(1, 61)}
    assert {i for i in ids if i.startswith("ins-")} == {f"ins-{n:03d}" for n in range(1, 81)}
    assert {i for i in ids if i.startswith("oos-")} == {f"oos-{n:03d}" for n in range(1, 41)}


def test_case_ids_filter_scores_only_those_ids(tmp_path):
    out = tmp_path / "m.json"
    assert eval_gate.main(["--case-ids", str(_BASELINE_V0_IDS), "--json", str(out)]) == 0
    metrics = json.loads(out.read_text(encoding="utf-8"))
    assert metrics["n"] == 250
    assert set(metrics["per_case"]) == eval_gate.load_case_ids(_BASELINE_V0_IDS)


def test_case_ids_file_with_unknown_id_fails_loudly(tmp_path):
    bad = tmp_path / "ids.txt"
    bad.write_text("# comment\nem-001\nem-does-not-exist\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        eval_gate.load_cases(case_ids=eval_gate.load_case_ids(bad))


def test_case_ids_file_ignores_comments_and_blank_lines(tmp_path):
    f = tmp_path / "ids.txt"
    f.write_text("# frozen\n\nem-001\n  em-002  \n", encoding="utf-8")
    assert eval_gate.load_case_ids(f) == {"em-001", "em-002"}
