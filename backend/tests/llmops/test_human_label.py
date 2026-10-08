"""Human labelling kit: blind stratified sample + Cohen's kappa."""

from __future__ import annotations

import csv

import pytest

from scripts import human_label


def test_kappa_perfect_and_chance():
    assert human_label.cohens_kappa(["A", "B", "A", "B"], ["A", "B", "A", "B"]) == 1.0
    # Observed agreement equal to chance agreement -> kappa 0.
    assert human_label.cohens_kappa(["A", "A", "B", "B"], ["A", "B", "A", "B"]) == 0.0


def test_kappa_known_value():
    a = ["E"] * 20 + ["A"] * 15 + ["O"] * 25
    b = ["E"] * 18 + ["O"] * 2 + ["A"] * 13 + ["O"] * 2 + ["O"] * 23 + ["A"] * 2
    k = human_label.cohens_kappa(a, b)
    assert 0.80 < k < 0.90


def test_kappa_undefined_when_one_class():
    assert human_label.cohens_kappa(["A", "A"], ["A", "A"]) is None


def test_class_mapping():
    assert human_label.reference_class("escalate") == "EMERGENCY"
    assert human_label.reference_class("answer") == "ANSWER"
    assert human_label.reference_class("no_answer") == "OTHER"
    assert human_label.reference_class("clarify") == "OTHER"
    assert human_label.human_class("Doctor ") == "OTHER"
    with pytest.raises(ValueError):
        human_label.human_class("maybe")


def test_sample_is_blind_stratified_and_deterministic():
    rows = human_label.build_sample(seed=7)
    assert len(rows) == sum(human_label.STRATA.values())
    assert rows == human_label.build_sample(seed=7)
    assert all(r["label"] == "" for r in rows)
    assert not any("expected" in k or "verdict" in k or "split" in k for k in rows[0])
    assert len({r["id"] for r in rows}) == len(rows)
    assert all(r["current_facts"] and r["question"] for r in rows)


def test_agreement_report_flags_safety_disagreements(tmp_path):
    rows = human_label.build_sample(seed=7)
    from careline.services.eval_gate import load_cases
    ref = {c["id"]: c["expected"]["verdict"] for c in load_cases()}
    to_human = {"escalate": "emergency", "answer": "answer", "clarify": "redirect", "no_answer": "doctor"}
    labels = {r["id"]: to_human[ref[r["id"]]] for r in rows}
    emergency_id = next(i for i in labels if ref[i] == "escalate")
    labels[emergency_id] = "answer"  # one unsafe human disagreement
    path = tmp_path / "labels-tester.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "label"])
        w.writeheader()
        w.writerows({"id": i, "label": l} for i, l in labels.items())
    report = human_label.agreement([path])
    assert "| tester | 60 | 59/60 |" in report
    assert emergency_id in report


def test_ai_labels_reported_separately_and_never_as_human(tmp_path):
    rows = human_label.build_sample(seed=7)
    path = tmp_path / "ai-labels-model.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "label"])
        w.writeheader()
        w.writerows({"id": r["id"], "label": "doctor"} for r in rows)
    report = human_label.agreement([], [path])
    assert "No human labels yet" in report
    assert "AI second labeller (NOT human)" in report
    assert report.index("AI second labeller") > report.index("Human labellers")
