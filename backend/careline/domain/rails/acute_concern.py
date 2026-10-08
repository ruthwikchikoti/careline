"""Acute-concern context layer — the fail-closed net under the rails (policy v3, v4).

The red-team run (2026-10-07) showed the v2 rail is a paraphrase matcher: any
vocabulary shift collapses token coverage, and fresh first-person emergencies
("my stools are black like tar since yesterday") fell through to the polite
out-of-scope redirect — fail-open. This module supplies the missing structural
signal: **a first-person statement containing an acute-distress term is never
safe to redirect**, even when no rail phrase matched. v4: the pre-LLM triage
(``domain/brain/triage.py``, shared by the Brain and the graph) runs
:func:`check_acute_concern` on EVERY question, and the scope gate re-checks it
for every scope; a hit escalates. (At v3 it ran only on the out-of-scope
branch, so "soft diet avoid spicy; I want to die" could be ANSWERED.)

Context guards keep the net precise (over-escalation is its own failure
mode, and the red team measured 19% benign FPs without them):

* **Definitional exemption** — "what is vitamin C", "side effects of steroids
  in general" are knowledge questions, not symptom reports.
* **History markers** — "collapsed last year", "during the operation",
  "runs in the family", "my father had a heart attack at 50" are reports
  about the past or other people; also used by the rail to suppress
  suppressible-concept hits. v4: **clause-scoped** (see
  :func:`unsuppressed_text`) and overridden by any present-tense marker in
  the same clause.
* **Explicit future-hypothetical framing** (v4, round 2) — ONLY "if I ever",
  "in case I ever", "what should I do if", "what happens if", "if I were to".
  "when/whenever/if/should + I/my X + have/get/feel" is NOT hypothetical: it
  is a RECURRING report ("when I have chest pain…") and stays live. And a
  hypothetical clause is never silenced outright: the triage turns a hit
  that only a hypothetical clause carries into a CLARIFY with the emergency
  line (``domain/brain/triage.py``), never an ANSWER.
* **Non-human subjects** (v4, round 2) — a comma/dash segment whose only
  subject is an animal ("my cat passed out on the sofa") is dropped from the
  live text.
* **Denial markers** — "I do not want to end my life" must not fire the
  ideation rail; v4 strips only the denied phrase (:func:`strip_denials`),
  so a second, undenied ideation phrase or an overdose count still fires.

Deterministic, keyless, pure — mirrored into ``backend/policies/
red-flags.v4.yaml`` and enforced in sync by ``tests/llm/test_prompt_registry.py``.

Owner: Priyanshu (scope ``safety``).
"""

from __future__ import annotations

import re

from careline.domain.rails.normalise import text_variants

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
    r"\bwhat\s+(?:is|are|was|were)\b(?!\s+(?:wrong|happening|going\s+on)\b)"
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
    ("collapse", r"\bcollapse[ds]?\b|\bfaint(?:ed|ing|s)?\b|\bpass(?:ed|ing)?\s+out\b|\bblacking\s+out\b|\bblackouts?\b"),
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
    ("wrong_medication", r"\btook\b.{0,40}\binstead\s+of\s+(?:my|mine)\b|\bby\s+(?:mistake|accident)\b|\baccidentally\s+(?:took|swallowed|drank|ate|had)\b|\bwrong\s+(?:tablet|medicine|dose|bottle)\b"),
    ("bp_crisis", r"\b(?:1[7-9]\d|2\d\d)\s*(?:over|/)\s*1[0-4]\d\b"),
)

_ACUTE_RES = tuple((label, re.compile(pat, re.IGNORECASE)) for label, pat in ACUTE_TERM_PATTERNS)


# ---------------------------------------------------------------------------
# Clause-scoped context (policy v4)
# ---------------------------------------------------------------------------
# v3 applied history/denial markers to the WHOLE message: one "at 150", "used
# to" or "would never harm myself" anywhere silenced a present-tense emergency
# elsewhere in the same message (round-1 adversarial review). v4 scopes every
# marker to the clause that carries it:
#
# * the question is split into clauses at sentence punctuation, at contrast
#   connectors (but / however / although / though / whereas / except), and
#   before a fresh "now" ("…, now I have chest pain");
# * a clause is *suppressed* only if it carries a history/hypothetical/
#   definitional marker AND carries no present-tense marker (now, today,
#   "I have", "I'm having", "can't stop", …);
# * suppressible rail concepts and the acute-concern terms are matched only
#   against the unsuppressed clauses; every other concept still sees the
#   whole message.

# Distant past, other people, or meta-discussion: the event is not happening
# now to this caller. Deliberately EXCLUDES recent markers ("since last
# night", "this morning", "now") — recent stays dangerous. v4 drops the bare
# "at NN" age marker (it swallowed "my pulse is at 150"); an age needs
# "at age NN" / "when I was NN".
HISTORY_MARKERS: tuple[str, ...] = (
    r"last\s+year", r"last\s+month", r"years?\s+ago", r"a\s+year\s+ago",
    r"\bhistory\b", r"family\s+history", r"runs\s+in\s+the\s+family",
    r"grandfather", r"grandmother",
    r"my\s+(?:father|mother|dad|mom|brother|sister|wife|husband|uncle|aunt)\s+(?:had|has\s+had|had\s+a|died)\b",
    r"during\s+the\s+(?:operation|surgery|procedure)",
    r"when\s+they\s+took\s+my\b", r"last\s+time\b", r"\bused\s+to\b",
    r"injury\s+questions",
    r"\bat\s+(?:the\s+)?age\s+(?:of\s+)?\d{1,3}\b",
    r"\bwhen\s+i\s+was\s+(?:\d{1,3}|a\s+(?:kid|child|baby|teenager|boy|girl))\b",
    r"\bin\s+the\s+past\b", r"\bas\s+a\s+(?:kid|child|baby|teenager)\b",
    r"nearly\s+(?:fainted?|collapsed|passed\s+out)", r"almost\s+(?:fainted?|collapsed|passed\s+out)",
)

# EXPLICIT future-hypothetical framing only (v4, round 2). The unreleased v4
# draft also listed "(if|when|whenever|in case|should) (I|my X|he|she|they)
# (feel|get|have|…)" — that silenced RECURRING reports ("When I have chest
# pain is my review still in 2 weeks?" was ANSWERED; sev-0). "when/whenever +
# subject + have/get/feel" describes something that happens to the caller,
# so it is a present report. A clause carrying one of these markers (and no
# present marker) is "softened": the triage may downgrade its ESCALATE to a
# CLARIFY that carries the emergency line — never to an ANSWER.
_HYPO_SUBJECT = r"(?:i|we|my\s+\w+|he|she|they|someone)"
_HYPO_VERB = (
    r"(?:feel|feels|get|gets|have|has|develop\w*|start\w*|notice\w*|become\w*"
    r"|experience\w*|faint|collapse|pass\s+out)"
)
HYPOTHETICAL_MARKERS: tuple[str, ...] = (
    rf"\bif\s+{_HYPO_SUBJECT}\s+ever\b",
    rf"\bin\s+case\s+{_HYPO_SUBJECT}\s+ever\b",
    rf"\bif\s+{_HYPO_SUBJECT}\s+(?:were|was)\s+to\b",
    rf"\bwhat\s+(?:should|do|would|must|can)\s+(?:i|we)\s+do\s+if"
    rf"(?:\s+{_HYPO_SUBJECT}(?:\s+ever)?(?:\s+{_HYPO_VERB})?)?\b",
    rf"\bwhat\s+happens\s+if(?:\s+{_HYPO_SUBJECT}(?:\s+ever)?(?:\s+{_HYPO_VERB})?)?\b",
)

# Present-tense / happening-now markers. A clause carrying one is NEVER
# suppressed, whatever history or hypothetical marker it also carries.
PRESENT_MARKERS: tuple[str, ...] = (
    r"\b(?:right\s+)?now\b", r"\btoday\b", r"\btonight\b", r"\bcurrently\b",
    r"\bat\s+the\s+moment\b", r"\bthis\s+(?:morning|afternoon|evening)\b",
    r"\bsince\s+(?:yesterday|last\s+night|this\s+morning|morning|an?\s+hour|\d+)",
    r"\bcan'?t\s+stop\b",
    r"\bi\s+(?:have|feel|keep)\b(?!\s+(?:had\b|been\b|(?:a\s+|an\s+)?(?:family\s+)?history\b|no\b|never\b))",
    r"\bi'?ve\s+got\b",
    r"\b(?:i'?m|i\s+am|he'?s|he\s+is|she'?s|she\s+is|it'?s|it\s+is|they'?re|they\s+are)\s+"
    r"(?:having|getting|feeling|bleeding|vomiting|shaking|fitting|seizing|choking|struggling"
    r"|gasping|wheezing|losing|going|turning|not\s+breathing)\b",
    r"\b(?:going|gone|turning)\s+(?:blue|grey|gray|purple)\b",
)

# Explicit self-harm denials. v4: each marker consumes the DENIED PHRASE
# itself, and the rail strips only those spans before matching ideation — so
# "I would never harm myself but I want to kill myself" still fires on the
# second clause. A denial can never suppress an overdose/ingestion count.
DENIAL_MARKERS: tuple[str, ...] = (
    r"\b(?:not|never)\s+(?:feeling\s+)?suicidal\b",
    r"\b(?:do(?:n'?t|\s+not)|did(?:n'?t|\s+not)|would\s+never|will\s+never|won'?t|never)\s+"
    r"(?:(?:want|plan|intend|going)\s+to\s+)?(?:end|kill|harm|hurt)\s+"
    r"(?:my\s*self|my\s+(?:own\s+)?life|it\s+all)\b",
    r"\bdo(?:n'?t|\s+not)\s+want\s+to\s+(?:die|be\s+dead)\b",
    r"\bnot\s+going\s+to\s+(?:hurt|kill|end|harm)\s+(?:my\s*self|my\s+(?:own\s+)?life|it\s+all)\b",
    r"\bno\s+(?:plans?|intention|thoughts?)\s+(?:to|of)\s+(?:harm\w*|end\w*|kill\w*|hurt\w*)\s+"
    r"(?:my\s*self|my\s+(?:own\s+)?life|it\s+all)\b",
    r"\b(?:not|never)\s+(?:self[- ]?harm\w*|harm(?:ed|ing)?\s+my\s*self)\b",
)

# A segment (split at commas and dashes inside a clause) whose subject is an
# animal and which names no human subject is dropped from the live text:
# "My cat passed out on the sofa after dinner, so cute — when is my
# follow-up?" is not a report about a person. The danger-concept invariant
# (symptom_report.mentions_danger_concept) still sees it, so such a message
# can be redirected or clarified but never ANSWERED.
NON_HUMAN_SUBJECT_MARKERS: tuple[str, ...] = (
    r"\b(?:my|our|the|his|her|their|a)\s+(?:pet\s+|little\s+|old\s+)?(?:cats?|kittens?|kitty|dogs?"
    r"|pupp(?:y|ies)|pups?|pets?|parrots?|birds?|budgies?|hamsters?|rabbits?|bunn(?:y|ies)"
    r"|goldfish|fish|horses?|cows?|goats?|tortoises?|turtles?|guinea\s+pigs?)\b",
)
# Human subject tokens that keep a segment live even if an animal is named
# ("my cat scratched me and I'm bleeding", "the dog bit my son"). Third-person
# pronouns are deliberately absent: next to an animal they usually refer to it
# ("my dog's breathing is noisy when he sleeps").
_HUMAN_TOKEN_RE = re.compile(
    r"\b(?:i|i'?m|im|i'?ve|me|myself|we|us|mujhe|mera|meri|mere"
    r"|son|daughter|baby|child|kid|toddler|husband|wife|partner|mum|mom|mother|dad|father"
    r"|grandson|granddaughter|grandmother|grandfather|brother|sister)\b",
    re.IGNORECASE,
)
# Any personal reference — what makes a definitional clause NOT impersonal
# ("what causes my chest pain?" stays live for the rail).
_PERSONAL_RE = re.compile(
    r"\b(?:i|i'?m|im|i'?ve|me|my|myself|we|us|our|he|she|him|her|his|they|them|their"
    r"|mujhe|mera|meri|mere)\b",
    re.IGNORECASE,
)
_SEGMENT_SPLIT = re.compile(r"\s*[,\u2014\u2013]\s*|\s+-{1,2}\s+")

_HISTORY_RE = re.compile("|".join(f"(?:{p})" for p in HISTORY_MARKERS), re.IGNORECASE)
_HYPOTHETICAL_RE = re.compile("|".join(f"(?:{p})" for p in HYPOTHETICAL_MARKERS), re.IGNORECASE)
_PRESENT_RE = re.compile("|".join(f"(?:{p})" for p in PRESENT_MARKERS), re.IGNORECASE)
_DENIAL_RE = re.compile("|".join(f"(?:{p})" for p in DENIAL_MARKERS), re.IGNORECASE)
_NON_HUMAN_RE = re.compile(
    "|".join(f"(?:{p})" for p in NON_HUMAN_SUBJECT_MARKERS), re.IGNORECASE
)

_CLAUSE_SPLIT = re.compile(
    r"[.;!?\n]+"
    r"|,?\s*\b(?=(?:but|however|although|though|whereas|except)\b)"
    r"|(?:,|\band\b)\s*(?=now\b)",
    re.IGNORECASE,
)

# Rail concepts whose hits history context can suppress. In the RED-FLAG RAIL
# (literal/structural regexes + semantic library) every other concept —
# ideation, overdose/poisoning, breathing, anaphylaxis, bleeding — is matched
# on the whole message (ideation on the denial-stripped text), so "I tried to
# end my life last year" still escalates there. The acute-concern net below
# is different: it matches ALL of its terms on the live text, so a history
# clause does silence its "bleeding"/"breathless" terms (the rail and the
# danger-concept invariant still see them).
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
# Regex pseudo-concepts matched on the denial-stripped text (self-harm
# vocabulary). v4 adds the ideation phrase families.
DENIAL_REGEX_CONCEPTS: frozenset[str] = frozenset({"suicid", "self_harm", "ideation"})


def split_clauses(question: str) -> list[str]:
    """Split a message into clauses (see the v4 note above)."""
    return [c.strip() for c in _CLAUSE_SPLIT.split(question or "") if c and c.strip()]


def _is_present(clause: str) -> bool:
    # Only the EXPLICIT future-hypothetical framing span is removed first
    # ("what should I do if I have chest pain" → the framing consumes "I have").
    # Recurring "when I have / when I feel" is no longer a marker, so its
    # "I have"/"I feel" stays and counts as present.
    return bool(_PRESENT_RE.search(_HYPOTHETICAL_RE.sub(" ", clause)))


def _clause_suppressed(
    clause: str, *, definitional: bool, hypothetical: bool, impersonal_definitional: bool
) -> bool:
    marked = bool(
        _HISTORY_RE.search(clause)
        or (hypothetical and _HYPOTHETICAL_RE.search(clause))
        or (definitional and _DEFINITIONAL.search(clause))
        or (
            impersonal_definitional
            and _DEFINITIONAL.search(clause)
            and not _PERSONAL_RE.search(clause)
        )
    )
    return marked and not _is_present(clause)


def _drop_non_human_segments(clause: str) -> str:
    """Drop comma/dash segments whose only subject is an animal."""
    if not _NON_HUMAN_RE.search(clause):
        return clause
    kept = [
        seg for seg in _SEGMENT_SPLIT.split(clause)
        if seg and not (
            _NON_HUMAN_RE.search(seg)
            and not _HUMAN_TOKEN_RE.search(_NON_HUMAN_RE.sub(" ", seg))
        )
    ]
    return ", ".join(kept)


def unsuppressed_text(
    question: str,
    *,
    definitional: bool = False,
    hypothetical: bool = True,
    impersonal_definitional: bool = False,
) -> str:
    """The question with history (and, by default, explicit future-
    hypothetical; optionally definitional) clauses and animal-subject segments
    removed — what suppressible concepts are matched against.

    ``hypothetical=False`` keeps hypothetical clauses live: the triage uses it
    to find danger that ONLY a hypothetical clause carries (→ CLARIFY with the
    emergency line, never ANSWER). ``impersonal_definitional`` suppresses a
    definitional clause only when it names no human subject ("What causes
    chest pain in general?" but not "what causes my chest pain?").

    Clauses are re-joined with ". " so a cross-clause regex cannot bridge a
    suppressed clause into a live one.
    """
    live: list[str] = []
    for c in split_clauses(question):
        if _clause_suppressed(
            c,
            definitional=definitional,
            hypothetical=hypothetical,
            impersonal_definitional=impersonal_definitional,
        ):
            continue
        c = _drop_non_human_segments(c)
        if c:
            live.append(c)
    return ". ".join(live)


def strip_denials(question: str) -> str:
    """The question with only the explicitly denied self-harm phrases removed."""
    return _DENIAL_RE.sub(" ", question or "")


def history_suppressed(question: str) -> bool:
    """True when EVERY clause of the question is history/explicit-hypothetical context
    (a whole-message convenience; the rails use the clause-scoped
    :func:`unsuppressed_text`)."""
    return bool(question) and not unsuppressed_text(question)


def denial_suppressed(question: str) -> bool:
    """True when the question contains an explicit self-harm denial."""
    return bool(_DENIAL_RE.search(question or ""))


def check_acute_concern(
    question: str, *, soften_hypothetical: bool = True, context: bool = True
) -> str | None:
    """Return the first acute-distress term label if this is a first-person
    acute concern, else ``None``.

    Fires only when: first person present (patient describing themselves or
    family) AND an acute-distress term in a clause that is not definitional,
    history or explicit-hypothetical context (unless that clause is
    present-tense). v4: runs pre-LLM on EVERY question (Brain + graph triage),
    is re-checked by the scope gate for every scope, and scans the original
    and the typo-normalised text.

    ``soften_hypothetical=False`` keeps explicit-hypothetical clauses live;
    ``context=False`` disables every context guard (the danger-concept
    invariant's "suppressed or not" view; first person is still required).
    """
    if not question:
        return None
    for text in text_variants(question):
        if not _FIRST_PERSON.search(text):
            continue
        live = (
            unsuppressed_text(text, definitional=True, hypothetical=soften_hypothetical)
            if context else text
        )
        if not live:
            continue
        for label, rx in _ACUTE_RES:
            if rx.search(live):
                return label
    return None


__all__ = [
    "ACUTE_TERM_PATTERNS",
    "HISTORY_MARKERS",
    "HYPOTHETICAL_MARKERS",
    "NON_HUMAN_SUBJECT_MARKERS",
    "PRESENT_MARKERS",
    "DENIAL_MARKERS",
    "SUPPRESSIBLE_CONCEPTS",
    "SUPPRESSIBLE_REGEX_CONCEPTS",
    "DENIAL_REGEX_CONCEPTS",
    "check_acute_concern",
    "split_clauses",
    "unsuppressed_text",
    "strip_denials",
    "history_suppressed",
    "denial_suppressed",
]
