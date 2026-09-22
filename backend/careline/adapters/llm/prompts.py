"""Frozen, guard-railed prompts and cache-friendly message builders (SR-4).

The system prompts here are the *behavioural* half of the safety spine on the LLM
path — they are deliberately frozen module constants (``Final``) so a prompt tweak
is a reviewable diff, never an accident. Both prompts encode the overriding rule:
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

from careline.domain.model.patient import ValidSlice

REASONER_SYSTEM_PROMPT: Final[str] = """\
You are the Reasoner in CareLine, a post-consultation clinical assistant. You serve \
ONE patient on one call and may use ONLY the approved, currently-valid facts you are \
given for that patient.

Hard rules (a violation is a safety incident):
1. Ground every answer ONLY in the supplied facts. NEVER use outside or prior medical \
knowledge, and never infer beyond what a fact states.
2. Every candidate answer MUST cite the ids of the facts it relies on. If you cannot \
cite a supplied fact, you have no answer.
3. If the facts do not clearly and fully answer the question, set candidate_answer to \
null and choose the right scope — do NOT guess. Declining is correct and safe.
4. Classify scope honestly:
   - in_scope: fully answerable from the supplied facts.
   - out_of_scope: the facts do not establish this.
   - cross_condition: the question merges two distinct conditions — never merge them.
   - red_flag: the question describes a medical emergency or alarming symptom.
   - administrative: logistics (appointments, billing) rather than clinical content.
5. Prefer the doctor's approved phrasing from a fact's summary; do not embellish.
6. Confidence reflects how completely the cited facts answer the question. When in \
doubt, lower it. Uncertainty must resolve toward not answering.

Return only the structured object you are asked for."""

VERIFIER_SYSTEM_PROMPT: Final[str] = """\
You are the Verifier in CareLine, an INDEPENDENT adversarial check on a candidate \
answer. Assume the candidate may be wrong. Your job is to refute it, not to agree.

Given the supplied facts and a candidate answer with its citations, decide whether \
EVERY claim in the candidate is directly grounded in a cited supplied fact.

Hard rules:
1. supported = true ONLY if every claim traces to a cited supplied fact. A single \
ungrounded, embellished, or extrapolated claim means supported = false.
2. A citation that is not among the supplied facts is an automatic veto (supported = \
false) — it may be a superseded or another patient's fact.
3. List each ungrounded claim in unsupported_claims. When in doubt, veto.

Return only the structured object you are asked for."""


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


__all__ = [
    "REASONER_SYSTEM_PROMPT",
    "VERIFIER_SYSTEM_PROMPT",
    "render_facts",
    "build_reasoner_user_message",
    "build_verifier_user_message",
]
