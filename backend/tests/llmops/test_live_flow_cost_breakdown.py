"""Live-flow cost headline is split by who handled the question (final
evaluator finding ``peer-cost-headline-averaging``).

The v7 report's "$0.00024 per portal question" averaged in rail-caught
emergencies that cost $0 (no LLM call). ``cost_breakdown`` reports both the
mean over every portal question and the mean over the MODEL-HANDLED questions
(those that made an LLM call). Computed here from the committed live run —
no network, nothing re-run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.live_flow_check import _markdown, cost_breakdown

_REPORT = Path(__file__).resolve().parents[2] / "evals" / "reports" / "live-flow-gpt-4o-mini.json"


def test_cost_breakdown_from_the_committed_live_run():
    log = json.loads(_REPORT.read_text(encoding="utf-8"))
    b = cost_breakdown(log["monitoring"])
    assert b["questions"] == 18
    assert b["model_handled"] == 14
    assert b["no_llm_call"] == 4
    assert b["total_cost_usd"] == pytest.approx(0.004301)
    assert b["mean_per_question_usd"] == pytest.approx(0.004301 / 18)
    assert b["mean_per_model_handled_usd"] == pytest.approx(0.004301 / 14)


def test_cost_breakdown_fails_soft_on_missing_numbers():
    b = cost_breakdown({})
    assert b["mean_per_model_handled_usd"] is None
    assert b["mean_per_question_usd"] is None


def test_markdown_reports_both_cost_figures():
    log = json.loads(_REPORT.read_text(encoding="utf-8"))
    md = _markdown(log, 1.0)
    assert "Mean cost per model-handled question" in md
    assert "Mean cost per portal question, all" in md
    assert "no LLM call" in md
