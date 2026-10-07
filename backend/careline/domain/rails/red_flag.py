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
)

_RED_FLAG_RE = re.compile(
    "|".join(f"(?:{p})" for p in RED_FLAG_PATTERNS),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Semantic emergency detector (policy v2)
# ---------------------------------------------------------------------------
# A curated danger-phrase library matched by token coverage ⊕ character-trigram
# cosine — deterministic, keyless, and tolerant of everyday paraphrase. Runs
# only when the v1 regexes miss, as the second, denser net.

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
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _content_tokens(normalized: str) -> frozenset[str]:
    return frozenset(t for t in normalized.split() if t not in _STOPWORDS)


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
    """
    norm = _normalize(question)
    q_tokens = _content_tokens(norm)
    q_tri = _trigrams(norm)
    best_concept, best_score = None, 0.0
    for concept, p_tokens, p_tri in _PHRASE_VECTORS:
        if not p_tokens:
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

    Two nets, both deterministic and pre-LLM: the v1 literal regexes, then
    the v2 semantic detector for phrasings the regexes miss. A match means
    the turn goes straight to ESCALATE without ever invoking the Reasoner.
    """
    match = _RED_FLAG_RE.search(question)
    if match:
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
