"""Eval-gate hardening — accuracy-drop regression, split floors, missing metrics.

The gate is only as honest as its regression check. Three ways it could be
gamed before this slice, each pinned here:

* "blocks accuracy drops" was untrue — in-scope answer accuracy was
  informational only, so a change that answered fewer in-scope questions
  still passed. It is now a regression metric (must not DROP vs baseline;
  no absolute floor on the keyless twin).
* deleting cases shrinks a split until its gate is vacuous — per-split
  minimum case counts (>= floors, since the set may only grow).
* a metric present in the baseline but absent from the current run was
  silently skipped — now a failure.

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import json
import re

import pytest

from careline.services import eval_gate


@pytest.fixture(scope="module")
def full_run():
    patients, now = eval_gate._load_seed()
    cases = eval_gate.load_cases()
    results = eval_gate.run_keyless(cases, patients, now)
    return eval_gate.score(results)


def test_score_exposes_enforced_in_scope_accuracy_and_keeps_old_key(full_run):
    assert "in_scope_answer_accuracy" in full_run
    # Backwards compatible: the informational key is still emitted, same value.
    assert full_run["in_scope_answer_accuracy"] == full_run[
        "in_scope_answer_accuracy_informational"
    ]


def test_accuracy_drop_vs_baseline_fails():
    baseline = {"in_scope_answer_accuracy": 0.30}
    current = {"in_scope_answer_accuracy": 0.21}
    failures = eval_gate.regression_check(current, baseline)
    assert any("in_scope_answer_accuracy" in f for f in failures)


def test_accuracy_equal_or_better_passes():
    assert eval_gate.regression_check(
        {"in_scope_answer_accuracy": 0.25}, {"in_scope_answer_accuracy": 0.25}
    ) == []
    assert eval_gate.regression_check(
        {"in_scope_answer_accuracy": 0.40}, {"in_scope_answer_accuracy": 0.25}
    ) == []


def test_legacy_baseline_informational_key_is_honoured():
    # The committed baselines predate the enforced key; the alias must still bite.
    baseline = {"in_scope_answer_accuracy_informational": 0.30}
    failures = eval_gate.regression_check({"in_scope_answer_accuracy": 0.10}, baseline)
    assert any("in_scope_answer_accuracy" in f for f in failures)


def test_metric_missing_from_current_fails_instead_of_skipping():
    baseline = {"missed_emergencies": 0, "ungrounded_answers": 0}
    current = {"missed_emergencies": 0}  # ungrounded_answers vanished
    failures = eval_gate.regression_check(current, baseline)
    assert any("ungrounded_answers" in f and "missing" in f for f in failures)


def test_none_current_value_counts_as_missing():
    failures = eval_gate.regression_check(
        {"in_scope_answer_accuracy": None}, {"in_scope_answer_accuracy": 0.2}
    )
    assert any("missing" in f for f in failures)


def test_max_metric_increase_still_fails():
    failures = eval_gate.regression_check(
        {"over_escalation_rate": 0.12}, {"over_escalation_rate": 0.09}
    )
    assert any("over_escalation_rate" in f for f in failures)


def test_split_floors_values():
    assert eval_gate.SPLIT_MIN_CASES == {
        "emergency": 60,
        "in_scope": 80,
        "out_of_scope": 40,
        "cross_patient": 20,
        "injection": 30,
        "superseded": 20,
    }


def test_split_below_floor_fails():
    splits = dict(eval_gate.SPLIT_MIN_CASES)
    splits["emergency"] = 59
    failures = eval_gate.check_split_floors(splits)
    assert any("emergency" in f for f in failures)


def test_split_missing_entirely_fails():
    splits = dict(eval_gate.SPLIT_MIN_CASES)
    del splits["injection"]
    assert any("injection" in f for f in eval_gate.check_split_floors(splits))


def test_split_growth_is_allowed():
    splits = {k: v + 25 for k, v in eval_gate.SPLIT_MIN_CASES.items()}
    assert eval_gate.check_split_floors(splits) == []


def test_committed_eval_set_meets_floors(full_run):
    assert eval_gate.check_split_floors(full_run["splits"]) == []


def test_eval_set_digest_covers_every_case_file():
    digest = eval_gate.eval_set_digest()
    files = {p.name for p in eval_gate._CASES_DIR.glob("*.jsonl")}
    assert set(digest["files"]) == files
    for value in digest["files"].values():
        assert re.fullmatch(r"[0-9a-f]{64}", value)
    assert re.fullmatch(r"[0-9a-f]{64}", digest["combined"])


def test_main_blocks_on_accuracy_drop(tmp_path, capsys):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"in_scope_answer_accuracy": 0.99}), encoding="utf-8")
    code = eval_gate.main(["--baseline", str(baseline)])
    assert code == 1
    out = capsys.readouterr()
    assert "in_scope_answer_accuracy" in out.out + out.err


def test_main_report_prints_eval_set_sha256(tmp_path, capsys):
    out_json = tmp_path / "m.json"
    code = eval_gate.main(["--json", str(out_json)])
    assert code == 0
    printed = capsys.readouterr().out
    assert "sha256" in printed
    metrics = json.loads(out_json.read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{64}", metrics["eval_set_sha256"])
    # schema stays backwards compatible
    for key in ("n", "splits", "missed_emergencies", "in_scope_answer_accuracy_informational"):
        assert key in metrics


# v3 = the last baseline before the set grew (compared on the 250 shared case
# ids); v4 = the newest accepted baseline, the one CI gates against.
@pytest.mark.parametrize("name", ["after-policy-v3.json", "after-policy-v4.json"])
def test_main_passes_against_committed_baseline(name):
    path = eval_gate._BACKEND_ROOT / "evals" / "reports" / name
    assert eval_gate.main(["--baseline", str(path)]) == 0

