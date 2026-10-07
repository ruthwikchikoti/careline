"""Acute-concern context layer — the fail-closed net under the rails (policy v3).

The red-team run (2026-10-07) showed the v2 rail is a paraphrase matcher: any
vocabulary shift collapses token coverage, and fresh first-person emergencies
("my stools are black like tar since yesterday") fell through to the polite
out-of-scope redirect — fail-open. This module supplies the missing structural
signal: **a first-person statement containing an acute-distress term is never
safe to redirect**, even when no rail phrase matched. The scope gate consults
:func:`check_acute_concern` before any redirect; a hit escalates.

Three context guards keep the net precise (over-escalation is its own failure
mode, and the red team measured 19% benign FPs without them):

* **Definitional exemption** — "what is vitamin C", "side effects of steroids
  in general" are knowledge questions, not symptom reports.
* **History markers** — "collapsed last year", "during the operation",
  "runs in the family", "my father had a heart attack at 50" are reports
  about the past or other people; also used by the rail to suppress
  suppressible-concept hits (``history_suppressed``).
* **Denial markers** — "I do not want to end my life" must not fire the
  ideation rail (``denial_suppressed``); scoped to self-harm concepts only,
  because "it is not a stroke, but my words are slurring" is still an
  emergency.

Deterministic, keyless, pure — mirrored into ``backend/policies/
red-flags.v3.yaml`` and enforced in sync by ``tests/llm/test_prompt_registry.py``.

Owner: Priyanshu (scope ``safety``).
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# First-person + definitional context
# ---------------------------------------------------------------------------

# "my" deliberately includes family reports: "my husband fell", "my son drank…".
_FIRST_PERSON = re.compile(
    r"\b(?:i|i'?m|im|i'?ve|ive|i'?ll|me|mine|my)\b", re.IGNORECASE
)

# Knowledge questions about medicine are not symptom reports. Checked BEFORE
# the acute terms so "what are the side effects of steroids in general?" with
# an acute-adjacent word never escalates on this layer.
_DEFINITIONAL = re.compile(
    r"\bwhat\s+(?:is|are|was|were)\b"
    r"|\bhow\s+(?:does|do|did)\s+\S+\s+(?:work|happen|differ)"
    r"|\bin\s+general\b|\bgenerally\b"
    r"|\bfor\s+(?:people|everyone|most\s+people)\b"
    r"|\bside\s+effects?\s+of\b|\bdifference\s+between\b"
    r"|\bnormal\s+(?:blood\s+)?(?:sugar|range|level)"
    r"|\bshould\s+(?:i|my\s+\S+)\s+also\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Acute-distress vocabulary (deliberately narrow: distress words only —
# conditions ("blood pressure"), drugs, and plain "pain" are excluded so
# routine medication/diet questions never land here).
# ---------------------------------------------------------------------------

ACUTE_TERM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("bleeding", r"\bbleed(?:ing|s)?\b|\bblood\s+in\s+(?:my\s+)?(?:vomit|stool|urine|phlegm|sputum)"),
    ("breathing_distress", r"\bbreathless\w*|\bcannot\s+breathe|\bcan'?t\s+breathe|\bunable\s+to\s+breathe|\bshortness\s+of\s+breath|\bbreath\s+(?:keeps?\s+)?(?:stopping|getting\s+(?:hard|worse|difficult)|is\s+short)"),
    ("chest", r"\bchest\b"),
    ("collapse", r"\bcollapse[ds]?\b|\bfaint(?:ed|ing|s)?\b|\bpassed?\s+out\b|\bblacking\s+out\b|\bblackouts?\b"),
    ("dizzy", r"\bdizzy\w*|\bdizziness\b|\broom\s+is\s+spinning\b|\bspinning\s+head\b"),
    ("vomiting", r"\bvomit\w*|\bthrowing\s+up\b|\bbeing\s+sick\b|\bthrew\s+up\b"),
    ("seizure", r"\bseizure\w*|\bconvulsion\w*|\bfitting\b|\bjerking\b"),
    ("reduced_consciousness", r"\bunconscious\b|\bunresponsive\b|\bnot\s+responding\b|\bcannot\s+be\s+woken\b|\bcan'?t\s+(?:be\s+)?woken?\b|\bwon'?t\s+wake\b|\bcouldn'?t\s+be\s+woken\b|\bcan'?t\s+wake\s+(?:him|her|them)\b|\bkeep\s+my\s+eyes\s+open\b"),
    ("neuro_focal", r"\bnumb\w*|\bslurr\w*|\bweak\w*\s+(?:all\s+over|one\s+side|down\s+one)\b|\bdrooping\b|\bdroop\b|\bone\s+side\b.{0,30}\b(?:weak|numb|droop)"),
    ("rash_swelling", r"\brash\w*|\bswelling\b|\bswollen\b|\bhives\b|\bcoming\s+out\s+in\b"),
    ("fever", r"\bfever\w*|\bburning\s+up\b|\bboiling\b"),
    ("hypoglycaemia", r"\bsugar\s+(?:has\s+|keeps\s+|is\s+|was\s+)?(?:dropped|dropping|low)\b|\bglucose\s+(?:has\s+|is\s+)?(?:dropped|low)\b|\bhypo\b|\bsugar\s+is\s+(?:[23]\d|4\d)\b"),
    ("tremor", r"\btrembl\w*|\bshak(?:ing|es)\b|\bshivering\s+uncontrollably\b"),
    ("gi_bleed", r"\bblack\s+(?:stool|tarry)\b|\btarry\s+stool\b|\bblack\s+like\s+tar\b|\bcoffee[\s-]ground"),
    ("head_injury", r"\bhit\s+my\s+head\b|\bfell\s+(?:down|on)\b|\bfallen\b|\bfell\s+and\b|\b(?:splitting|worst|severe|sudden|thunderclap)\s+headache"),
    ("poisoning", r"\bkerosene\b|\bbleach\b|\bpesticide\b|\bweed\s?-?killer\b|\bcleaning\s+liquid\b|\bpoison\w*|\boverdosed?\b|\bketone\w*\s*(?:strips?|test)?\s*(?:is|are|show\w*)?\s*high|\bnail\s+polish\s+remover|\bfruity\s+breath"),
    ("cyanosis", r"\b(?:turned|turning|is|look\w*)\s+(?:blue|bluish|grey|gray)\b"),
    ("wrong_medication", r"\btook\b.{0,40}\binstead\s+of\s+(?:my|mine)\b|\bby\s+mistake\b|\bwrong\s+(?:tablet|medicine|dose|bottle)\b"),
    ("bp_crisis", r"\b(?:1[7-9]\d|2\d\d)\s*(?:over|/)\s*1[0-4]\d\b"),
)

_ACUTE_RES = tuple((label, re.compile(pat, re.IGNORECASE)) for label, pat in ACUTE_TERM_PATTERNS)


def check_acute_concern(question: str) -> str | None:
    """Return the first acute-distress term label if this is a first-person
    acute concern, else ``None``.

    Fires only when: first person present (patient describing themselves or
    family) AND an acute-distress term AND no definitional/history context.
    """
    if _DEFINITIONAL.search(question):
        return None
    if not _FIRST_PERSON.search(question):
        return None
    if _HISTORY_RE.search(question):
        return None
    for label, rx in _ACUTE_RES:
        if rx.search(question):
            return label
    return None


# ---------------------------------------------------------------------------
# History / denial context (shared with the rail's suppression logic)
# ---------------------------------------------------------------------------

# Distant past, other people, or meta-discussion: the event is not happening
# now to this caller. Deliberately EXCLUDES recent markers ("since last
# night", "this morning", "now") — recent stays dangerous.
_HISTORY_MARKERS: tuple[str, ...] = (
    r"last\s+year", r"last\s+month", r"years?\s+ago", r"a\s+year\s+ago",
    r"\bhistory\b", r"family\s+history", r"runs\s+in\s+the\s+family",
    r"grandfather", r"grandmother",
    r"my\s+(?:father|mother|dad|mom|brother|sister|wife|husband|uncle|aunt)\s+(?:had|has\s+had|had\s+a)\b",
    r"during\s+the\s+(?:operation|surgery|procedure|procedure\b)",
    r"when\s+they\s+took\s+my\b", r"last\s+time\b", r"\bused\s+to\b",
    r"injury\s+questions", r"at\s+(?:[4-9]\d|1\d\d)\b",
    r"nearly\s+(?:fainted?|collapsed|passed\s+out)", r"almost\s+(?:fainted?|collapsed|passed\s+out)",
)
_HISTORY_RE = re.compile("|".join(f"(?:{p})" for p in _HISTORY_MARKERS), re.IGNORECASE)

# Explicit self-harm denials — suppress only self-harm/ideation concepts.
_DENIAL_MARKERS: tuple[str, ...] = (
    r"not\s+suicidal", r"do(?:n'?t)?\s+not\s+want\s+to\s+(?:end|die|kill)",
    r"don'?t\s+want\s+to\s+(?:end|die|kill)", r"not\s+going\s+to\s+(?:hurt|kill|end)",
    r"no\s+plans\s+to\s+(?:harm|end|kill)", r"would\s+never\s+(?:harm|end|kill)",
    r"never\s+harm\s+(?:myself|me)", r"don'?t\s+want\s+to\s+(?:harm|hurt)\s+myself",
)
_DENIAL_RE = re.compile("|".join(f"(?:{p})" for p in _DENIAL_MARKERS), re.IGNORECASE)

# Rail concepts whose hits history context can suppress. Acute-now concepts
# (ideation, overdose/poisoning, breathing, anaphylaxis, bleeding) are NEVER
# history-suppressible: "I tried to end my life last year" still escalates.
SUPPRESSIBLE_CONCEPTS: frozenset[str] = frozenset(
    {
        "reduced_consciousness", "cardiac_signs", "head_injury",
        "chest_pain_cardiac", "stroke", "dvt_swelling", "joint_redness_sepsis",
        "severe_undifferentiated", "seizure",
    }
)
# v1-regex pseudo-concepts that history can also suppress — these are the
# *mapped* names from red_flag._PATTERN_CONCEPTS (cardiac_signs, …), not the
# raw pattern slugs.
SUPPRESSIBLE_REGEX_CONCEPTS: frozenset[str] = frozenset(
    {
        "cardiac_signs", "head_injury", "chest_pain_cardiac", "stroke",
        "seizure", "reduced_consciousness",
    }
)
# v1 regex pseudo-concepts denial markers suppress (self-harm vocabulary).
DENIAL_REGEX_CONCEPTS: frozenset[str] = frozenset({"suicid", "self_harm"})


def history_suppressed(question: str) -> bool:
    """True when the question reports the distant past / other people."""
    return bool(_HISTORY_RE.search(question))


def denial_suppressed(question: str) -> bool:
    """True when the question explicitly denies self-harm intent."""
    return bool(_DENIAL_RE.search(question))


__all__ = [
    "ACUTE_TERM_PATTERNS",
    "SUPPRESSIBLE_CONCEPTS",
    "SUPPRESSIBLE_REGEX_CONCEPTS",
    "DENIAL_REGEX_CONCEPTS",
    "check_acute_concern",
    "history_suppressed",
    "denial_suppressed",
]
