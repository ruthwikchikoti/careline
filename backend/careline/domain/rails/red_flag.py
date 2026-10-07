"""Pre-LLM red-flag matcher and multi-condition tripwire (VI-1).

The Triage agent's first line of defense: a keyword/phrase matcher that fires
**before** any LLM call.  If a patient's question mentions chest pain,
breathing difficulty, or any other emergency phrase, the turn is immediately
routed to ESCALATE — no model involved, no confidence needed, fully
deterministic.

The pattern list is mirrored into a **versioned policy artifact**
(``backend/policies/red-flags.vN.yaml``, registered in
``backend/prompts/manifest.yaml``) so every release of the rail is diffable,
hash-stamped in eval reports, and gated in CI like any other prompt change.
The domain stays pure (no file I/O): the artifact and this constant are held
in sync by ``tests/llm/test_prompt_registry.py`` — changing one without the
other fails the suite, and a policy bump without an eval run fails the gate.

The multi-condition tripwire detects when a question spans multiple clinical
condition groups (e.g. diabetic + post-op diet), which must escalate because
the agent cannot safely merge guidance across conditions.

Design choice: false positives are *safe* (the doctor handles it); false
negatives are not.  Err on the side of inclusion.

Owner: Priyanshu (scope ``safety``).
"""

from __future__ import annotations

import math
import re
from collections import Counter

from careline.domain.rails.acute_concern import (
    DENIAL_REGEX_CONCEPTS,
    SUPPRESSIBLE_CONCEPTS,
    SUPPRESSIBLE_REGEX_CONCEPTS,
    denial_suppressed,
    history_suppressed,
)
from careline.domain.rails.emergency_phrases import PHRASE_LIBRARY, SEMANTIC_THRESHOLD

# ---------------------------------------------------------------------------
# Red-flag patterns (emergency keywords)
# ---------------------------------------------------------------------------
# Each pattern is a case-insensitive regex fragment.  They are compiled into a
# single alternation for a single-pass scan of the question.
# v1 = the original hand-written list (see policies/red-flags.v1.yaml).

RED_FLAG_PATTERNS: tuple[str, ...] = (
    r"chest\s+pain",
    r"difficulty\s+breathing",
    r"breathing\s+difficulty",
    r"can'?t\s+breathe",
    r"shortness\s+of\s+breath",
    r"unconscious",
    r"unresponsive",
    r"seizure",
    r"convulsion",
    r"bleeding\s+heavily",
    r"heavy\s+bleeding",
    r"suicid",                  # matches suicide, suicidal
    r"self[- ]?harm",
    r"heart\s+attack",
    r"stroke\s+symptom",
    r"anaphyla",                # anaphylaxis, anaphylactic
    r"choking",
    r"overdose",
    r"poison",
    r"severe\s+allergic",
    r"head\s+injury",
    r"loss\s+of\s+consciousness",
    # -- v3 additions: inflections, punctuation forms, and number crises the
    # red-team run showed were missed (see tests/brain/test_red_flag_novel.py).
    r"cannot\s+breathe",
    r"can\s+not\s+breathe",
    r"shortness[\s-]+of[\s-]+breath",
    r"breathless\s+(?:at|while|when)\s+(?:rest|lying|sitting|speaking|talking|walking|climbing)",
    r"black\s+like\s+tar",
    r"black\s+(?:tarry\s+)?stool",
    r"coffee[\s-]ground",
    r"(?:sugar|glucose)\s+(?:has\s+|keeps\s+|is\s+|was\s+)?(?:dropped|dropping|low)\b",
    r"(?:sugar|glucose)\b[^.]{0,30}\b(?:[23]\d|4\d)\b(?!\d)",
    r"\b(?:1[7-9]\d|2\d\d)\s*(?:over|/)\s*1[0-4]\d\b",
    r"ketone\w*\s*(?:strips?|test|levels?)?\s*(?:is|are|show\w*|reading)?\s*high",
    r"nail\s+polish\s+remover",
    r"fruity\s+breath",
    r"(?:drank|drinking|swallowed|swallowing|ate|eaten|got\s+into)\b[^.]{0,40}\b(?:kerosene|bleach|pesticide|weed\s?-?killer|cleaning\s+liquid|detergent|camphor|naphthalene|petrol)\b",
    r"(?:cannot|could\s?not|won'?t)\s+be\s+woken",
    r"not\s+responding",
    r"\bslurr(?:ed|ing)\b",
    r"bleeding\s+(?:through|past|soaking)\s+(?:the\s+)?(?:bandage|dressing)",
    r"\btook\b[^.]{0,40}(?:instead\s+of\s+(?:my|mine)|by\s+mistake|wrong\s+(?:tablet|medicine|dose|bottle))",
    r"(?:turned|turning)\s+(?:blue|bluish|grey|gray)",
)

# Concept map for the literal patterns — used by the v3 context suppression
# (history/denial markers in acute_concern.py). Default pseudo-concept is the
# pattern's own slug.
_PATTERN_CONCEPTS: dict[str, str] = {
    r"heart\s+attack": "cardiac_signs",
    r"stroke\s+symptom": "stroke",
    r"head\s+injury": "head_injury",
    r"chest\s+pain": "chest_pain_cardiac",
    r"seizure": "seizure",
    r"convulsion": "seizure",
    r"unconscious": "reduced_consciousness",
    r"unresponsive": "reduced_consciousness",
    r"loss\s+of\s+consciousness": "reduced_consciousness",
    r"suicid": "suicid",
    r"self[- ]?harm": "self_harm",
}

_COMPILED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (
        re.compile(p, re.IGNORECASE),
        _PATTERN_CONCEPTS.get(p, "literal:" + re.sub(r"\\[a-z]+\+?|\W+", "", p)[:24]),
    )
    for p in RED_FLAG_PATTERNS
)

# Kept for the shadow comparison's regex-only v1 variant and the policy sync.
_RED_FLAG_RE = re.compile(
    "|".join(f"(?:{p})" for p in RED_FLAG_PATTERNS),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Semantic emergency detector (policy v2)
# ---------------------------------------------------------------------------
# A curated danger-phrase library matched by token coverage ⊕ character-trigram
# cosine — deterministic, keyless, and tolerant of everyday paraphrase. Runs
# only when the regexes miss, as the second, denser net.

_STOPWORDS = frozenset(
    """
    i me my mine myself we our ours you your yours he him his she her it its
    they them their a an the and or but if then than because so of in on at to
    for with from by as is are was were be been being am do does did done have
    has had not no nor too very just now this that these those there here what
    when where which who whom how why can could will would shall should may
    might must about into over under again also get got feel felt like
    """.split()
)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _content_tokens(normalized: str) -> frozenset[str]:
    # len > 1: apostrophe-splitting must not mint stray 1-char tokens ("t").
    return frozenset(
        t for t in normalized.split() if t not in _STOPWORDS and len(t) > 1
    )


def _trigrams(normalized: str) -> Counter:
    padded = f"  {normalized} "
    return Counter(padded[i : i + 3] for i in range(len(padded) - 2))


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(count * b.get(g, 0) for g, count in a.items() if g in b)
    return dot / (
        math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    )


# Phrase side precomputed once at import: (concept, tokens, trigram counter).
_PHRASE_VECTORS: tuple[tuple[str, frozenset[str], Counter], ...] = tuple(
    (concept, _content_tokens(_normalize(phrase)), _trigrams(_normalize(phrase)))
    for concept, phrases in PHRASE_LIBRARY.items()
    for phrase in phrases
)


def semantic_red_flag(question: str) -> tuple[str, float] | None:
    """Score the question against the danger-phrase library.

    Returns ``(concept, score)`` for the best-scoring phrase above
    :data:`SEMANTIC_THRESHOLD`, else ``None``. Score = 0.6 × token coverage
    (how much of a danger phrase the question contains) ⊕ 0.4 × character-
    trigram cosine (robustness to small wording changes). Pure — no model,
    no key, no network.

    v3: history/denial context (acute_concern) suppresses matching concepts —
    "my grandfather collapsed last year" does not fire reduced_consciousness.
    """
    if not question:
        return None
    norm = _normalize(question)
    q_tokens = _content_tokens(norm)
    q_tri = _trigrams(norm)
    hist = history_suppressed(question)
    den = denial_suppressed(question)
    best_concept, best_score = None, 0.0
    for concept, p_tokens, p_tri in _PHRASE_VECTORS:
        if not p_tokens:
            continue
        if hist and concept in SUPPRESSIBLE_CONCEPTS:
            continue
        if den and concept == "suicidal_ideation":
            continue
        coverage = len(q_tokens & p_tokens) / len(p_tokens)
        sim = 0.6 * coverage + 0.4 * _cosine(q_tri, p_tri)
        if sim > best_score:
            best_concept, best_score = concept, sim
    if best_score >= SEMANTIC_THRESHOLD:
        return best_concept, round(best_score, 4)
    return None


def check_red_flag(question: str) -> str | None:
    """Return the matched red-flag signal if found, else ``None``.

    Two nets, both deterministic and pre-LLM: the literal regexes (v1 + v3
    additions), then the semantic detector (v2) for phrasings the regexes
    miss. Both are context-suppressed (v3): history/denial markers veto
    suppressible concepts, so distant-past mentions and self-harm denials
    do not trip the rail. A match means the turn goes straight to ESCALATE
    without ever invoking the Reasoner.
    """
    if not question:
        return None
    hist = history_suppressed(question)
    den = denial_suppressed(question)
    for pattern, concept in _COMPILED_PATTERNS:
        match = pattern.search(question)
        if match is None:
            continue
        if hist and concept in SUPPRESSIBLE_REGEX_CONCEPTS:
            continue
        if den and concept in DENIAL_REGEX_CONCEPTS:
            continue
        return match.group(0)
    semantic = semantic_red_flag(question)
    if semantic is not None:
        return f"semantic:{semantic[0]}"
    return None


# ---------------------------------------------------------------------------
# Multi-condition tripwire
# ---------------------------------------------------------------------------
# Condition keyword groups.  If a question touches ≥2 distinct groups, it
# spans conditions and must escalate (cross-condition interaction is out of
# scope for the agent).

_CONDITION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cardiac", ("heart", "cardiac", "blood pressure", "bp ", "hypertension", "cholesterol")),
    ("diabetes", ("diabetes", "diabetic", "blood sugar", "glucose", "insulin", "metformin")),
    ("surgery", ("surgery", "surgical", "post-op", "post-operative", "operation", "incision")),
    ("respiratory", ("asthma", "inhaler", "breathing", "copd", "respiratory", "nebulizer")),
    ("renal", ("kidney", "renal", "dialysis", "creatinine")),
    ("neurological", ("seizure", "epilepsy", "migraine", "neurological")),
    ("gastric", ("gastric", "ulcer", "acid reflux", "gerd", "stomach")),
    ("psychiatric", ("anxiety", "depression", "psychiatric", "mental health")),
)


def check_multi_condition(question: str) -> tuple[bool, list[str]]:
    """Detect whether a question spans ≥2 distinct clinical condition groups.

    Returns ``(is_cross_condition, matched_groups)``.  If ``True``, the turn
    must escalate — the agent cannot safely merge guidance across conditions.
    """
    q_lower = question.lower()
    matched: list[str] = []
    for group_name, keywords in _CONDITION_GROUPS:
        if any(kw in q_lower for kw in keywords):
            matched.append(group_name)
    return (len(matched) >= 2, matched)


__all__ = [
    "RED_FLAG_PATTERNS",
    "check_red_flag",
    "check_multi_condition",
]
