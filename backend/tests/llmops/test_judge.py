"""LLM-as-judge — structured faithfulness verdicts, keyless twin, fail closed.

The judge scores an ANSWER against ONLY the text of the facts it cites. Two
implementations share one contract: ``OpenAIJudge`` (versioned prompt
``prompts/judge/v1.md``, structured output) and ``KeylessJudge`` (a
deterministic token-overlap groundedness check, so online evaluation works
offline). Either way, uncertainty is never scored as faithful.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from careline.adapters.llm import usage
from careline.adapters.llm.judge import (
    JudgeDTO,
    JudgeUnavailable,
    KeylessJudge,
    OpenAIJudge,
    build_judge,
    build_judge_user_message,
    load_judge_prompt,
)
from careline.adapters.llm.prompt_registry import _manifest, reload

from .fakes import OracleOpenAI

FACTS = [
    ("ravi-med-1", "Paracetamol 500mg twice daily for post-op pain."),
    ("ravi-ins-1", "Soft diet for 2 weeks post-surgery; avoid spicy food."),
]


def test_judge_prompt_is_registered_versioned_and_hash_checked():
    reload()
    entry = _manifest()["prompts"]["judge"]
    assert entry["version"] == "v1"
    assert entry["file"] == "prompts/judge/v1.md"
    art = load_judge_prompt()
    assert art.stamp.startswith("judge@v1+")
    assert "cited facts" in art.text.lower()


def test_judge_dto_is_strict():
    schema = JudgeDTO.model_json_schema()
    assert schema.get("additionalProperties") is False
    assert set(schema["properties"]) == {"faithful", "score", "unsupported_claims"}
    with pytest.raises(Exception):
        JudgeDTO(faithful=True, score=1.5, unsupported_claims=[])


def test_user_message_carries_only_cited_fact_text():
    msg = build_judge_user_message(answer="Take paracetamol 500mg twice daily.", facts=FACTS[:1])
    assert "Paracetamol 500mg" in msg
    assert "Soft diet" not in msg


# -- keyless twin ------------------------------------------------------------


def test_keyless_grounded_answer_is_faithful():
    v = KeylessJudge().judge(
        answer="Paracetamol 500mg twice daily for post-op pain.", facts=FACTS[:1]
    )
    assert v.faithful is True
    assert v.score >= 0.9
    assert v.unsupported_claims == ()


def test_keyless_invented_claim_is_flagged():
    v = KeylessJudge().judge(
        answer=(
            "Paracetamol 500mg twice daily for post-op pain. "
            "You may also take ibuprofen 800mg with alcohol tonight."
        ),
        facts=FACTS[:1],
    )
    assert v.faithful is False
    assert any("ibuprofen" in c.lower() for c in v.unsupported_claims)
    assert v.score < 0.9


def test_keyless_no_facts_or_empty_answer_is_never_faithful():
    assert KeylessJudge().judge(answer="anything", facts=[]).faithful is False
    assert KeylessJudge().judge(answer="", facts=FACTS).faithful is False


def test_keyless_number_mismatch_is_unfaithful():
    v = KeylessJudge().judge(answer="Paracetamol 1000mg twice daily.", facts=FACTS[:1])
    assert v.faithful is False


# -- OpenAI judge (fake client) ---------------------------------------------


def test_openai_judge_maps_structured_output_and_records_usage():
    usage.reset()
    client = OracleOpenAI(judge_faithful=False)
    v = OpenAIJudge(client=client, model="gpt-4o-mini").judge(
        answer="Paracetamol 500mg.", facts=FACTS[:1]
    )
    assert v.faithful is False and v.score == 0.2
    assert v.unsupported_claims == ("made-up claim",)
    assert client.calls == ["JudgeDTO"]
    recs = usage.records()
    assert recs[-1].agent == "judge" and recs[-1].model == "gpt-4o-mini"


def test_openai_judge_fails_closed_on_provider_error():
    client = OracleOpenAI(raise_on=("JudgeDTO",))
    with pytest.raises(JudgeUnavailable):
        OpenAIJudge(client=client).judge(answer="x", facts=FACTS[:1])


def test_openai_judge_fails_closed_on_missing_parse():
    class _NoParse:
        class responses:  # noqa: N801 - SDK shape
            @staticmethod
            def parse(**_kw):
                return SimpleNamespace(output_parsed=None, usage=None)

    with pytest.raises(JudgeUnavailable):
        OpenAIJudge(client=_NoParse()).judge(answer="x", facts=FACTS[:1])


def test_openai_judge_skips_call_when_nothing_to_ground_on():
    client = OracleOpenAI()
    v = OpenAIJudge(client=client).judge(answer="x", facts=[])
    assert v.faithful is False and client.calls == []


def test_build_judge_picks_keyless_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(build_judge(), KeylessJudge)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert isinstance(build_judge(), KeylessJudge)


def test_build_judge_picks_openai_with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert isinstance(build_judge(), OpenAIJudge)
