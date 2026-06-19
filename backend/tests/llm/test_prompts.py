"""SR-4 — guard-railed prompts and cache-friendly message builders."""

from __future__ import annotations

from datetime import datetime, timezone

from careline.adapters.llm import prompts
from careline.domain.model.fact import Medication
from careline.domain.model.patient import ValidSlice
from careline.domain.model.temporal import Validity

NOW = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)


def _slice() -> ValidSlice:
    med = Medication(
        id="med-1",
        validity=Validity(effective_from=datetime(2026, 6, 1, tzinfo=timezone.utc)),
        summary="Take 500mg paracetamol every 6 hours.",
        name="paracetamol",
    ).approve("dr-1", datetime(2026, 6, 1, tzinfo=timezone.utc))
    return ValidSlice(as_of=NOW, facts=(med,))


def test_reasoner_prompt_encodes_core_guardrails():
    p = prompts.REASONER_SYSTEM_PROMPT.lower()
    assert "only" in p and "cite" in p
    assert "null" in p  # decline path
    assert "cross_condition" in p and "red_flag" in p


def test_verifier_prompt_is_adversarial_and_vetoes():
    p = prompts.VERIFIER_SYSTEM_PROMPT.lower()
    assert "independent" in p
    assert "supported = false" in p or "veto" in p


def test_render_facts_tags_each_fact_with_its_id():
    rendered = prompts.render_facts(_slice())
    assert "[med-1]" in rendered
    assert "paracetamol" in rendered


def test_render_facts_states_empty_slice_explicitly():
    rendered = prompts.render_facts(ValidSlice(as_of=NOW, facts=()))
    assert "no approved" in rendered.lower()


def test_reasoner_message_is_cache_friendly_question_last():
    msg = prompts.build_reasoner_user_message(question="what is my dose?", context=_slice())
    assert "[med-1]" in msg
    assert "what is my dose?" in msg
    # The most-variable content (the question) must come after the fact block.
    assert msg.index("PATIENT QUESTION") > msg.index("[med-1]")


def test_verifier_message_includes_candidate_and_citations():
    msg = prompts.build_verifier_user_message(
        question="dose?",
        candidate_answer="Take 500mg paracetamol.",
        citations=["med-1"],
        context=_slice(),
    )
    assert "Take 500mg paracetamol." in msg
    assert "med-1" in msg
    assert msg.index("PATIENT QUESTION") > msg.index("CANDIDATE ANSWER")
