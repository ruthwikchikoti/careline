"""Online monitor ``human_feedback`` section — aggregation, overwrite, alerts.

Human feedback is the second online-evaluation channel next to the sampled
LLM-as-judge: patient thumbs (helpful rate) and doctor review (doctor-rated
accuracy over ANSWER turns). The monitor keeps only booleans + verdicts keyed
by an opaque hash of the turn id — never comment / note text, never the turn
id itself.

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import json

from careline.adapters.llm.judge import KeylessJudge
from careline.domain.enums import Verdict
from careline.services import online_monitor
from careline.services.online_monitor import OnlineMonitor


def _monitor(window: int = 100) -> OnlineMonitor:
    return OnlineMonitor(window=window, judge=KeylessJudge(), judge_sample_rate=0.0)


def test_empty_section_has_no_rates_and_no_alerts():
    hf = _monitor().snapshot()["human_feedback"]
    assert hf["patient_ratings"] == 0
    assert hf["patient_helpful_rate"] is None
    assert hf["doctor_reviews"] == 0
    assert hf["doctor_rated_accuracy"] is None


def test_patient_helpful_rate_and_overwrite():
    m = _monitor()
    m.record_patient_feedback(turn_id="t1", verdict=Verdict.ANSWER, helpful=True)
    m.record_patient_feedback(turn_id="t2", verdict="clarify", helpful=False)
    m.record_patient_feedback(turn_id="t2", verdict="clarify", helpful=True)  # re-rate
    hf = m.snapshot()["human_feedback"]
    assert hf["patient_ratings"] == 2
    assert hf["patient_helpful"] == 2
    assert hf["patient_helpful_rate"] == 1.0


def test_doctor_accuracy_counts_answer_turns_only():
    m = _monitor()
    m.record_doctor_review(turn_id="a1", verdict=Verdict.ANSWER, correct=True)
    m.record_doctor_review(turn_id="a2", verdict=Verdict.ANSWER, correct=False)
    m.record_doctor_review(turn_id="e1", verdict=Verdict.ESCALATE, correct=False)
    hf = m.snapshot()["human_feedback"]
    assert hf["doctor_reviews"] == 3
    assert hf["doctor_reviews_answer"] == 2
    assert hf["doctor_correct_answer"] == 1
    assert hf["doctor_rated_accuracy"] == 0.5


def test_doctor_accuracy_alert_needs_ten_reviews():
    m = _monitor()
    for i in range(9):
        m.record_doctor_review(turn_id=f"a{i}", verdict="answer", correct=i < 7)
    assert not any("doctor-rated" in a for a in m.snapshot()["alerts"])
    m.record_doctor_review(turn_id="a9", verdict="answer", correct=False)  # 7/10
    assert any("doctor-rated accuracy" in a for a in m.snapshot()["alerts"])


def test_no_doctor_alert_at_or_above_ninety_percent():
    m = _monitor()
    for i in range(10):
        m.record_doctor_review(turn_id=f"a{i}", verdict="answer", correct=i != 0)  # 9/10
    assert not any("doctor-rated" in a for a in m.snapshot()["alerts"])


def test_patient_helpful_alert_needs_twenty_ratings():
    m = _monitor()
    for i in range(19):
        m.record_patient_feedback(turn_id=f"p{i}", verdict="answer", helpful=i < 10)
    assert not any("patient helpful" in a for a in m.snapshot()["alerts"])
    m.record_patient_feedback(turn_id="p19", verdict="answer", helpful=False)  # 10/20
    assert any("patient helpful rate" in a for a in m.snapshot()["alerts"])


def test_window_bounds_feedback_memory():
    m = _monitor(window=5)
    for i in range(12):
        m.record_patient_feedback(turn_id=f"p{i}", verdict="answer", helpful=True)
    assert m.snapshot()["human_feedback"]["patient_ratings"] == 5


def test_snapshot_holds_no_turn_ids():
    m = _monitor()
    m.record_patient_feedback(turn_id="turn-secret-123", verdict="answer", helpful=True)
    m.record_doctor_review(turn_id="turn-secret-456", verdict="answer", correct=True)
    dumped = json.dumps(m.snapshot())
    assert "turn-secret" not in dumped


def test_module_level_functions_never_raise():
    online_monitor.reset_monitor(_monitor())
    try:
        online_monitor.record_patient_feedback(turn_id="x", verdict="answer", helpful=True)
        online_monitor.record_doctor_review(turn_id="x", verdict="answer", correct=True)
        online_monitor.record_patient_feedback(turn_id=None, verdict=None, helpful=None)  # type: ignore[arg-type]
        hf = online_monitor.snapshot()["human_feedback"]
        assert hf["patient_ratings"] == 1 and hf["doctor_reviews"] == 1
    finally:
        online_monitor.reset_monitor()
