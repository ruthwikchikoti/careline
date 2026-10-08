"""LLM eval slice — real Brain + OpenAI adapters + judge, fully keyless here.

Every test injects a fake OpenAI client (``tests/llmops/fakes.py``); nothing
touches the network. What is pinned:

* exit codes: 2 ONLY when no API key is configured (and says why), 1 on a gate
  trip, 0 on pass;
* provider errors are counted and fail closed (the Brain escalates, the gate
  trips) — a broken provider can never read as a green run;
* the on-disk response cache: unchanged inputs/versions are never re-billed,
  and a prompt/policy stamp change misses;
* the LLM-slice gates (in-scope accuracy >= 0.85, judge faithfulness >= 0.90)
  plus every keyless safety gate.
"""

from __future__ import annotations

import json

import pytest

from careline.adapters.llm import usage
from careline.services import eval_gate, llm_eval

from .fakes import OracleOpenAI, oracle_from_cases


@pytest.fixture()
def cache_path(tmp_path):
    return tmp_path / "cache.sqlite"


@pytest.fixture()
def cases():
    return eval_gate.load_cases()


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    usage.reset()


# -- exit-code contract -------------------------------------------------------


def test_exit_2_only_without_key(monkeypatch, capsys, cache_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    code = eval_gate.main(["--mode", "llm", "--cache-path", str(cache_path)])
    assert code == 2
    err = capsys.readouterr().err
    assert "OPENAI_API_KEY" in err and "SKIPPED" in err


def test_blank_key_is_no_key(monkeypatch, cache_path):
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert eval_gate.main(["--mode", "llm", "--cache-path", str(cache_path)]) == 2


def test_pass_with_oracle_client(cases, cache_path, tmp_path):
    out = tmp_path / "llm.json"
    code = llm_eval.main(
        ["--cache-path", str(cache_path), "--json", str(out), "--judge-sample", "1.0"],
        client=oracle_from_cases(cases),
    )
    metrics = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0, metrics.get("gate_failures")
    assert metrics["in_scope_answer_accuracy"] >= 0.85
    assert metrics["judge_faithfulness_rate"] >= 0.90
    assert metrics["judged"] > 0
    assert metrics["provider_errors"] == 0
    assert metrics["missed_emergencies"] == 0
    assert metrics["mode"] == "llm"
    assert metrics["model"] == "gpt-4o-mini"
    assert metrics["artifacts"]["judge"].startswith("judge@v1+")


def test_unfaithful_judge_trips_faithfulness_gate(cases, cache_path, tmp_path):
    out = tmp_path / "llm.json"
    code = llm_eval.main(
        ["--cache-path", str(cache_path), "--json", str(out), "--judge-sample", "1.0"],
        client=oracle_from_cases(cases, judge_faithful=False),
    )
    metrics = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1
    assert any("judge_faithfulness_rate" in f for f in metrics["gate_failures"])


def test_wrong_model_trips_accuracy_gate(cache_path, tmp_path):
    out = tmp_path / "llm.json"
    code = llm_eval.main(
        ["--cache-path", str(cache_path), "--json", str(out)],
        client=OracleOpenAI({}),  # declines everything
    )
    metrics = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1
    assert any("in_scope_answer_accuracy" in f for f in metrics["gate_failures"])


def test_provider_errors_are_counted_and_fail_closed(cases, cache_path, tmp_path):
    out = tmp_path / "llm.json"
    code = llm_eval.main(
        ["--cache-path", str(cache_path), "--json", str(out)],
        client=oracle_from_cases(cases, raise_on=("ProposalDTO",)),
    )
    metrics = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1
    assert metrics["provider_errors"] > 0
    # Fail closed: no case answered on a broken provider.
    assert metrics["answers"] == 0
    assert metrics["missed_emergencies"] == 0
    assert any("provider_errors" in f for f in metrics["gate_failures"])


def test_judge_errors_count_as_unfaithful(cases, cache_path, tmp_path):
    out = tmp_path / "llm.json"
    llm_eval.main(
        ["--cache-path", str(cache_path), "--json", str(out), "--judge-sample", "1.0"],
        client=oracle_from_cases(cases, raise_on=("JudgeDTO",)),
    )
    metrics = json.loads(out.read_text(encoding="utf-8"))
    assert metrics["judge_errors"] > 0
    assert metrics["judge_faithfulness_rate"] == 0.0


def test_no_answers_judged_fails_closed(cache_path, tmp_path):
    out = tmp_path / "llm.json"
    code = llm_eval.main(
        ["--cache-path", str(cache_path), "--json", str(out), "--judge-sample", "1.0"],
        client=OracleOpenAI({}),
    )
    metrics = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1
    assert metrics["judge_faithfulness_rate"] is None
    assert any("judge_faithfulness_rate" in f for f in metrics["gate_failures"])


# -- sampling / limits --------------------------------------------------------


def test_limit_and_splits_select_a_stratified_subset(cases):
    picked = llm_eval.select_cases(cases, splits=["in_scope", "emergency"], limit=5)
    assert len(picked) == 10
    assert {c["split"] for c in picked} == {"in_scope", "emergency"}


def test_judge_sample_is_deterministic_and_proportional(cases):
    ids = [c["id"] for c in cases]
    a = [i for i in ids if llm_eval.judge_sampled(i, 0.2)]
    b = [i for i in ids if llm_eval.judge_sampled(i, 0.2)]
    assert a == b
    assert 0.1 * len(ids) < len(a) < 0.3 * len(ids)
    assert all(llm_eval.judge_sampled(i, 1.0) for i in ids)
    assert not any(llm_eval.judge_sampled(i, 0.0) for i in ids)


def test_sampled_run_is_labelled_not_a_release_gate(cases, cache_path, tmp_path, capsys):
    llm_eval.main(
        ["--cache-path", str(cache_path), "--limit", "3"],
        client=oracle_from_cases(cases),
    )
    assert "sampled" in capsys.readouterr().out.lower()


# -- cache -------------------------------------------------------------------


def test_second_run_is_served_from_cache(cases, cache_path, tmp_path):
    client = oracle_from_cases(cases)
    args = ["--cache-path", str(cache_path), "--splits", "in_scope", "--judge-sample", "1.0"]
    llm_eval.main(args + ["--json", str(tmp_path / "a.json")], client=client)
    first_calls = len(client.calls)
    assert first_calls > 0
    llm_eval.main(args + ["--json", str(tmp_path / "b.json")], client=client)
    assert len(client.calls) == first_calls  # zero new provider calls
    b = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))
    assert b["cache_hits"] > 0 and b["cache_misses"] == 0
    assert b["cost_usd"] == 0.0  # nothing re-billed


def test_no_cache_flag_always_calls(cases, cache_path):
    client = oracle_from_cases(cases)
    args = ["--cache-path", str(cache_path), "--splits", "in_scope", "--no-cache"]
    llm_eval.main(args, client=client)
    n = len(client.calls)
    llm_eval.main(args, client=client)
    assert len(client.calls) == 2 * n


def test_cache_key_changes_with_stamps_model_and_question():
    base = dict(
        kind="ProposalDTO", model="gpt-4o-mini", stamps={"reasoner": "reasoner@v1+aaa"},
        patient_id="ravi-kumar", question="q", payload="p",
    )
    k = llm_eval.cache_key(**base)
    assert k == llm_eval.cache_key(**base)
    for field, value in (
        ("model", "gpt-4.1"),
        ("stamps", {"reasoner": "reasoner@v2+bbb"}),
        ("patient_id", "meera-shah"),
        ("question", "q2"),
        ("payload", "p2"),
    ):
        assert llm_eval.cache_key(**{**base, field: value}) != k


def test_errors_are_never_cached(cases, cache_path):
    broken = oracle_from_cases(cases, raise_on=("ProposalDTO",))
    llm_eval.main(["--cache-path", str(cache_path), "--splits", "in_scope"], client=broken)
    good = oracle_from_cases(cases)
    llm_eval.main(["--cache-path", str(cache_path), "--splits", "in_scope"], client=good)
    assert "ProposalDTO" in good.calls  # the failure did not poison the cache


def test_cache_dir_is_gitignored(tmp_path):
    cache = llm_eval.ResponseCache(tmp_path / "sub" / "c.sqlite")
    cache.put("k", {"a": 1})
    assert cache.get("k") == {"a": 1}
    assert (tmp_path / "sub" / ".gitignore").read_text().strip() == "*"
