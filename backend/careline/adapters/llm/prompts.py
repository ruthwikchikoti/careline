"""Versioned, guard-railed prompts and cache-friendly message builders (SR-4).

The system prompts here are the *behavioural* half of the safety spine on the LLM
path. They are versioned release artifacts under ``backend/prompts/`` (see
:mod:`careline.adapters.llm.prompt_registry`) — a prompt tweak is a reviewable
diff with a hash, never an accident. Both prompts encode the overriding rule:
answer **only** from the supplied facts, cite the fact ids, and when anything is
unsupported, serious, or uncertain, decline rather than guess.

The user-message builders are **cache-friendly by construction**: the large, stable
content (the system prompt is the cache anchor; the per-patient fact block is
next) comes before the small, most-variable content (the question), so providers
that cache prompt prefixes can reuse the bulk of the tokens across a call's turns.

Owner: Srujan (scope ``llm``). Consumed by the Anthropic (SR-5) and OpenAI (SR-7)
adapters; the offline twins do not use them.
"""

from __future__ import annotations

from typing import Final

from careline.adapters.llm.prompt_registry import load_prompt
from careline.domain.model.patient import ValidSlice

REASONER_SYSTEM_PROMPT: Final[str] = load_prompt("reasoner").text

VERIFIER_SYSTEM_PROMPT: Final[str] = load_prompt("verifier").text


def render_facts(context: ValidSlice) -> str:
    """Render the valid slice as an id-tagged, citable fact block.

    Each line is ``[<id>] (<kind>) <summary>`` so the model can cite ids exactly.
    An empty slice is stated explicitly — the model must then decline.
    """
    if context.is_empty:
        return "(no approved, currently-valid facts are available for this patient)"
    return "\n".join(
        f"[{fact.id}] ({fact.kind.value}) {fact.summary}" for fact in context.facts
    )


def build_reasoner_user_message(*, question: str, context: ValidSlice) -> str:
    """Build the Reasoner user message — facts first, question last (cache-friendly)."""
    return (
        f"APPROVED, CURRENTLY-VALID FACTS (as of {context.as_of.isoformat()}):\n"
        f"{render_facts(context)}\n\n"
        f"PATIENT QUESTION:\n{question}"
    )


def build_verifier_user_message(
    *,
    question: str,
    candidate_answer: str,
    citations: tuple[str, ...] | list[str],
    context: ValidSlice,
) -> str:
    """Build the Verifier user message — facts and candidate first, question last."""
    cited = ", ".join(citations) if citations else "(none)"
    return (
        f"APPROVED, CURRENTLY-VALID FACTS (as of {context.as_of.isoformat()}):\n"
        f"{render_facts(context)}\n\n"
        f"CANDIDATE ANSWER:\n{candidate_answer}\n\n"
        f"CITED FACT IDS: {cited}\n\n"
        f"PATIENT QUESTION:\n{question}"
    )


EXTRACTOR_SYSTEM_PROMPT: Final[str] = load_prompt("extractor").text


def build_extractor_user_message(*, transcript: str) -> str:
    """Build the Extractor user message — the raw transcript to structure."""
    return f"CONSULTATION TRANSCRIPT:\n{transcript}"


__all__ = [
    "REASONER_SYSTEM_PROMPT",
    "VERIFIER_SYSTEM_PROMPT",
    "EXTRACTOR_SYSTEM_PROMPT",
    "render_facts",
    "build_reasoner_user_message",
    "build_verifier_user_message",
    "build_extractor_user_message",
]
