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

Policy v4 (round-1 adversarial review, 2026-10-08) adds structural patterns
(``_V4_PATTERNS``), clause-scoped history/denial context, and typo
normalisation (``normalise.py``); see ``policies/red-flags.v4.yaml``. Round 2
(same unreleased v4): ``soften_hypothetical`` / ``context`` switches for the
triage's hypothetical-only probe and the gate's danger-concept invariant,
impersonal definitional clauses suppress suppressible concepts, and "heart
attack risk" is not an event. The structural backstop for phrasings no list
anticipates is ``symptom_report.py``.

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
    strip_denials,
    unsuppressed_text,
)
from careline.domain.rails.emergency_phrases import PHRASE_LIBRARY, SEMANTIC_THRESHOLD
from careline.domain.rails.normalise import TYPO_CANONICAL_TOKENS, text_variants

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
    # v4 round 2: "my heart attack risk score" is a record lookup, not an event.
    r"heart\s+attack(?!\s+risk\b)",
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

# v4 additions (round-1 adversarial review, 2026-10-08) as (concept, pattern).
# The concept decides which text a pattern is matched against (see
# check_red_flag): suppressible concepts see only the live clauses, ideation
# sees the denial-stripped text, an empty concept (literal) sees everything.
_V4_PATTERNS: tuple[tuple[str, str], ...] = (
    # STRUCTURAL rules — a verb/subject family plus an object family — rather
    # than the review's literal strings, so neighbouring phrasings are covered.
    # Suicidal-ideation phrase families (matched on the denial-stripped text).
    ("ideation", r"\b(?:want(?:ed)?|wanna|going|plan(?:ning)?|ready|wish)\s+(?:to\s+)?(?:die|be\s+dead)\b"),
    ("ideation", r"\bkill(?:ing)?\s+my\s*self\b"),
    ("ideation", r"\bend(?:ing)?\s+(?:my\s+(?:own\s+)?life|it\s+all)\b"),
    ("ideation", r"\bnot\s+worth\s+living\b|\bno\s+(?:reason|point)\s+(?:to|in)\s+(?:live|living|going\s+on)\b|\bbetter\s+off\s+dead\b"),
    ("ideation", r"\b(?:sleep|go\s+to\s+sleep|close\s+my\s+eyes)\s+and\s+never\s+wake\s+up\b|\bnever\s+wake\s+up\s+again\b"),
    # Not breathing (any subject — a bystander report).
    ("", r"\b(?:not|isn'?t|stopped|no\s+longer)\s+breathing\b"),
    # Too breathless to talk; an asthma attack in progress.
    ("", r"\b(?:can'?t|cannot|unable\s+to|struggling\s+to)\s+(?:speak|talk|say|finish|get\s+out)\b[^.]{0,20}\bsentences?\b|\btoo\s+breathless\s+to\s+(?:speak|talk)\b"),
    ("", r"\b(?:having|in\s+the\s+middle\s+of)\s+(?:an?\s+)?(?:bad\s+|severe\s+|major\s+|big\s+)?asthma\s+attack\b"),
    # Ingestion: a whole pack, or a count of tablets.
    ("", r"\b(?:took|taken|swallow\w*|ate|eaten|popped|consumed|downed|finished)\b[^.]{0,30}\b(?:whole|entire|full)\s+(?:\w+\s+)?(?:strip|bottle|pack\w*|box|blister|sheet|jar)\b"),
    ("", r"\b(?:took|taken|swallow\w*|ate|eaten|popped|consumed|downed)\b[^.]{0,20}\b(?:[1-9]\d+|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty)\s+(?:\w+\s+){0,2}(?:tablets?|pills?|capsules?|tabs|caps)\b"),
    ("", r"\b(?:took|taken|swallow\w*|ate|eaten|popped|consumed|downed)\b[^.]{0,20}\b(?:[3-9]|three|four|five|six|seven|eight|nine)\s+(?:\w+\s+){0,2}(?:tablets?|pills?|capsules?|tabs|caps)\b[^.]{0,20}\b(?:together|at\s+once|in\s+one\s+go|at\s+one\s+go|at\s+the\s+same\s+time)\b"),
    # A child + swallowed/drank + a non-food object.
    ("", r"\b(?:child|kid|son|daughter|baby|toddler|grandson|granddaughter|grandchild|infant|boy|girl)\b[^.]{0,30}\b(?:swallow\w*|drank|drunk|drinking|ate|eaten|eating|chewed|chewing|got\s+into)\b[^.]{0,40}\b(?:tablets?|pills?|capsules?|strip|batter(?:y|ies)|magnets?|coins?|cleaner|bleach|detergent|chemicals?|acid|kerosene|petrol|diesel|phenyl|soap|pods?|poison\w*|insecticide|pesticide|mosquito|naphthalene|camphor|sanitis\w*|sanitiz\w*|perfume|alcohol)\b"),
    ("", r"\b(?:drank|drunk|drinking|swallowed|swallowing|ate|eaten)\b[^.]{0,40}\b(?:floor\s+cleaner|toilet\s+cleaner|cleaner|batter(?:y|ies)|acid|phenyl|sanitis\w*|sanitiz\w*|rat\s+poison|insecticide|antifreeze)\b"),
    ("", r"\btook\b[^.]{0,40}\bby\s+accident\b|\baccidentally\s+(?:took|swallowed|drank|ate)\b"),
    # Airway: throat/tongue/lips swelling or tight; cannot swallow.
    ("", r"\b(?:throat|tongue|lips?)\b[^.]{0,12}\b(?:swell\w*|swollen|tight\w*|clos\w*)\b"),
    ("", r"\b(?:swell\w*|swollen|tight\w*)\s+(?:in\s+|of\s+)?(?:my\s+|the\s+|his\s+|her\s+)?(?:throat|tongue|lips?)\b"),
    ("", r"\b(?:can'?t|cannot|unable\s+to)\s+swallow\b(?!\s+(?:the\s+|my\s+|these\s+|big\s+|large\s+)?(?:tablets?|pills?|capsules?|medicines?))"),
    # Chest tightness / pressure, any subject.
    ("chest_pain_cardiac", r"\bchest\s+(?:is\s+|feels?\s+|feeling\s+|getting\s+|gone\s+|very\s+|so\s+)*(?:tight\w*|heavy|crushing|squeez\w*)\b"),
    ("chest_pain_cardiac", r"\b(?:tight\w*|heaviness|crushing)\s+(?:in|on|of|across)\s+(?:my\s+|the\s+|his\s+|her\s+)?chest\b"),
    # Blood from the gut or lungs.
    ("", r"\b(?:vomit\w*|throw\w*\s+up|threw\s+up|cough\w*(?:\s+up)?|spit\w*(?:\s+up)?|puk\w*)\s+(?:up\s+)?(?:some\s+|bright\s+red\s+|fresh\s+)?blood\b"),
    ("", r"\bblood\w*\s+(?:in|when|while)\s+(?:i\s+)?(?:my\s+)?(?:vomit|cough|spit)\w*"),
    # Wound / incision bleeding or dehiscence.
    ("", r"\b(?:wound|incision|stitch\w*|sutures?|scar|cut|drain|staples?|dressing)\b[^.]{0,15}\b(?:bleed\w*|gush\w*|pour\w*|spurt\w*)\b"),
    ("", r"\b(?:gush\w*|pour\w*|spurt\w*)\s+(?:out\s+)?(?:of\s+)?blood\b|\bblood\s+(?:is\s+)?(?:gush\w*|pour\w*|spurt\w*)"),
    ("", r"\b(?:stitch\w*|sutures?|wound|incision|staples?)\b[^.]{0,15}\b(?:burst|split|opened|open\s+up|came\s+(?:open|apart|undone)|come\s+(?:open|apart|undone)|popped|broke(?:n)?\s+open|torn|tore|gaping)\b"),
    # Fever + altered mental state; sudden confusion.
    ("", r"\bfever\w*\b[^.]{0,40}\b(?:confus\w*|drowsy|delirious|disorient\w*|stiff\s+neck)\b|\b(?:confus\w*|drowsy|delirious|disorient\w*|stiff\s+neck)\b[^.]{0,40}\bfever\w*"),
    ("", r"\b(?:suddenly|very|so|extremely)\s+(?:confused|disoriented)\b"),
    # Rigid abdomen.
    ("", r"\b(?:belly|abdomen|stomach|tummy)\b[^.]{0,15}\b(?:rigid|hard\s+as\s+(?:a\s+)?(?:board|rock)|board[\s-]like)\b|\brigid\s+(?:belly|abdomen|stomach|tummy)\b"),
    # Sudden neurological deficit (FAST).
    ("stroke", r"\bsudden(?:ly)?\b[^.]{0,30}\b(?:(?:can'?t|cannot|unable\s+to|couldn'?t)\s+(?:lift|move|raise|feel|see|speak|talk|walk|stand|understand)|(?:lost|losing|loss\s+of)\s+(?:my\s+|his\s+|her\s+|the\s+)?(?:vision|sight|speech|balance|feeling|movement|strength)|weak\w*|numb\w*|paraly\w*|blind\w*|garbled|slurr\w*|droop\w*)"),
    ("stroke", r"\b(?:lost|losing|loss\s+of)\s+(?:the\s+)?(?:vision|sight|eyesight)\b"),
    ("stroke", r"\b(?:speech|words?|talking|voice)\b[^.]{0,25}\b(?:garbled|jumbled|slurr\w*|nonsense|not\s+making\s+sense)\b"),
    ("stroke", r"\bface\b[^.]{0,20}\b(?:uneven|lopsided|droop\w*|crooked|twisted)\b"),
    ("stroke", r"\b(?:can'?t|cannot|unable\s+to)\s+(?:lift|move|raise|feel)\s+(?:my\s+|his\s+|her\s+)?(?:left|right|one)\s+(?:arm|leg|hand|side)\b"),
    # Reduced fetal movement.
    ("", r"\bbaby\b[^.]{0,25}\b(?:hasn'?t|has\s+not|not|stopped|isn'?t|is\s+not|no\s+longer|didn'?t|did\s+not)\s+(?:been\s+)?(?:mov\w*|kick\w*)"),
    ("", r"\b(?:reduced|less|no|fewer|decreased|stopped)\s+(?:(?:fetal|foetal|baby(?:'s)?)\s+(?:movements?|kicks?|kicking)|kicks?|kicking)\b"),
    # Cyanosis and can't-stop symptoms.
    ("", r"\b(?:lips?|tongue|fingers?|fingertips|nails?|face|skin)\b[^.]{0,15}\b(?:blue|bluish|purple)\b"),
    ("", r"\bcan'?t\s+stop\s+(?:wheez\w*|vomit\w*|bleed\w*|shak\w*|fitting|jerking|convuls\w*)"),
    # Transliterated Hindi / Hinglish danger terms.
    ("chest_pain_cardiac", r"\bseene?\s+(?:me|mein|mai|mei|main)\s+(?:dard|dabav|dabaav|jalan|bhaari|bhari)\b"),
    ("", r"\bsaa?ns\s+(?:nahi|nahin|nai|na)\b|\bsaa?ns\s+(?:lene\s+)?(?:me|mein|mai|mei|main)\s+(?:taklif|takleef|taqleef|dikkat|dikat|pareshani)\b|\bsaa?ns\s+(?:ruk|phool|ful)\w*"),
    ("reduced_consciousness", r"\bbehosh\w*"),
    ("", r"\b(?:khoon|khun)\s+(?:ki|ka|wali)\s+ult(?:i|ee|iyan|iyaan)\b|\bult(?:i|ee)\s+(?:me|mein|mai)\s+(?:khoon|khun)\b"),
    ("seizure", r"\bdaur[ae]\s+(?:pad|pada|padna|padne|aa|aaya|aya)\w*|\bmirgi\b"),
)

RED_FLAG_PATTERNS = RED_FLAG_PATTERNS + tuple(p for _, p in _V4_PATTERNS)

# Concept map for the literal patterns — used by the v3 context suppression
# (history/denial markers in acute_concern.py). Default pseudo-concept is the
# pattern's own slug.
_PATTERN_CONCEPTS: dict[str, str] = {
    r"heart\s+attack(?!\s+risk\b)": "cardiac_signs",
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
_PATTERN_CONCEPTS.update({p: c for c, p in _V4_PATTERNS if c})

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


def _score(q_tokens: frozenset[str], q_tri: Counter, p_tokens: frozenset[str], p_tri: Counter) -> float:
    coverage = len(q_tokens & p_tokens) / len(p_tokens)
    return 0.6 * coverage + 0.4 * _cosine(q_tri, p_tri)


def _views(question: str, *, soften_hypothetical: bool, context: bool) -> tuple[str, str]:
    """(live, undenied) texts. ``live`` drops history (+ explicit-hypothetical
    when softening) clauses, impersonal definitional clauses ("What causes
    chest pain in general?") and animal-subject segments; ``context=False``
    returns the question untouched for both (the danger-concept view)."""
    if not context:
        return question, question
    live = unsuppressed_text(
        question, hypothetical=soften_hypothetical, impersonal_definitional=True
    )
    return live, strip_denials(question)


def semantic_red_flag(
    question: str, *, soften_hypothetical: bool = True, context: bool = True
) -> tuple[str, float] | None:
    """Score the question against the danger-phrase library.

    Returns ``(concept, score)`` for the best-scoring phrase above
    :data:`SEMANTIC_THRESHOLD`, else ``None``. Score = 0.6 × token coverage
    (how much of a danger phrase the question contains) ⊕ 0.4 × character-
    trigram cosine (robustness to small wording changes). Pure — no model,
    no key, no network.

    v4 (clause-scoped context): a suppressible concept is scored only against
    the live clauses (history/hypothetical clauses without a present-tense
    marker removed), suicidal ideation only against the denial-stripped text,
    and every other concept against the whole question — "my grandfather
    collapsed last year" stays quiet, "I used to faint, now I collapsed"
    does not.
    """
    if not question:
        return None
    live, undenied = _views(question, soften_hypothetical=soften_hypothetical, context=context)
    views: dict[str, tuple[frozenset[str], Counter]] = {}
    for key, text in (
        ("full", question),
        ("live", live),
        ("undenied", undenied),
    ):
        norm = _normalize(text)
        views[key] = (_content_tokens(norm), _trigrams(norm) if norm else Counter())
    best_concept, best_score = None, 0.0
    for concept, p_tokens, p_tri in _PHRASE_VECTORS:
        if not p_tokens:
            continue
        if concept in SUPPRESSIBLE_CONCEPTS:
            q_tokens, q_tri = views["live"]
        elif concept == "suicidal_ideation":
            q_tokens, q_tri = views["undenied"]
        else:
            q_tokens, q_tri = views["full"]
        sim = _score(q_tokens, q_tri, p_tokens, p_tri)
        if sim > best_score:
            best_concept, best_score = concept, sim
    if best_score >= SEMANTIC_THRESHOLD:
        return best_concept, round(best_score, 4)
    return None


def _check_one(text: str, *, soften_hypothetical: bool, context: bool) -> str | None:
    live, undenied = _views(text, soften_hypothetical=soften_hypothetical, context=context)
    for pattern, concept in _COMPILED_PATTERNS:
        if concept in SUPPRESSIBLE_REGEX_CONCEPTS:
            target = live
        elif concept in DENIAL_REGEX_CONCEPTS:
            target = undenied
        else:
            target = text
        match = pattern.search(target)
        if match is not None:
            return match.group(0)
    semantic = semantic_red_flag(text, soften_hypothetical=soften_hypothetical, context=context)
    if semantic is not None:
        return f"semantic:{semantic[0]}"
    return None


def check_red_flag(
    question: str, *, soften_hypothetical: bool = True, context: bool = True
) -> str | None:
    """Return the matched red-flag signal if found, else ``None``.

    Two nets, both deterministic and pre-LLM: the literal/structural regexes
    (v1 + v3 + v4 additions), then the semantic detector (v2) for phrasings
    the regexes miss. Context is clause-scoped (v4): a history/hypothetical
    marker vetoes suppressible concepts only within its own clause and never
    in a present-tense clause; a self-harm denial removes only the denied
    phrase. Both the original and the typo-normalised question are scanned
    (normalisation can only add matches). A match means the turn goes
    straight to ESCALATE without ever invoking the Reasoner.

    ``soften_hypothetical=False`` keeps explicit-hypothetical clauses live
    (the triage's "is the danger only hypothetical?" probe); ``context=False``
    disables every context guard (the danger-concept invariant's view).
    """
    if not question:
        return None
    for text in text_variants(question):
        hit = _check_one(text, soften_hypothetical=soften_hypothetical, context=context)
        if hit is not None:
            return hit
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
    "TYPO_CANONICAL_TOKENS",
    "check_red_flag",
    "check_multi_condition",
]
