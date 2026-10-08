"""Structural current-symptom-report layer — fail-closed recall net (policy v4, round 2).

Why this exists: every rail before it is a phrase list (literal regexes, a
danger-phrase library, a short acute-term list). Reviewers' fresh, never-seen
emergency phrasings scored ~45-55% recall on v3 AND v4 — a phrase list only
generalises to the phrases its authors thought of. This layer stops listing
*phrases* and checks *structure*:

    (a subject reference: the caller — I / me / my — or someone they care
     for — my child/mum/husband, he / she, our …)
    AND (a broad symptom/body lexicon hit: chest pain/pressure/weight,
         bleeding/blood/clots/black stools, breathing/wheezing/choking/blue
         lips, faint/collapse/won't wake/drowsy, numb/heavy/can't move/
         double vision, seizure/fit/jerking, face/throat swelling, vomiting,
         fever + rash/stiff neck, racing heart + faint, pregnancy + bleeding/
         no movement, ingestion verb + quantity or non-food substance,
         ideation families, transliterated Hindi danger words)
    AND NOT a general-knowledge question with no subject ("what causes chest
        pain in general?", "tell me about seizures").

A hit ESCALATEs (scope RED_FLAG, risk :data:`SYMPTOM_REPORT_RISK`) with the
emergency line. This deliberately trades over-escalation for recall — per the
overriding rule, uncertainty resolves toward ESCALATE — and the trade is
measured by the eval gate's over-escalation metric (max 0.15).

Precision guards (each is a measured trade, not a guarantee):

* the same clause-scoped history / explicit-hypothetical context as the other
  rails (:func:`acute_concern.unsuppressed_text`), and animal-subject segments
  are dropped ("my cat passed out");
* non-event uses of body words are excluded in the patterns ("blood test",
  "blood pressure", "confused about my dose", "bad breath");
* a MILD qualifier ("a bit", "slightly", …) directly before a SOFT symptom
  (dizzy, drowsy, weak, nausea/vomiting) does not fire — but never softens a
  red-flag symptom ("a bit of chest pain" still fires);
* v5: transient-and-resolved framing ("it passes in a second", "it's gone
  now") softens only the mild ``dizziness`` label; "donate/give blood" is not
  bleeding; a body part in a present-progressive clause ("chest is paining")
  counts as an implicit subject.

Provenance: the lexicon was driven by DEV probes (tests/brain/
test_review_round2.py). Passing those probes is a fit, not generalisation
evidence; the eval set and fresh held-out probes are the measurement.

Also exports :func:`mentions_danger_concept` — every rail with ALL context
guards disabled — which the gate chain uses as its final invariant: a
question containing any danger concept, suppressed or not, never ends in
ANSWER.

Deterministic, keyless, pure — mirrored into ``backend/policies/
red-flags.v5.yaml`` (``context.symptom_report``) and held in sync by
``tests/llm/test_prompt_registry.py``.

Owner: Priyanshu (scope ``safety``).
"""

from __future__ import annotations

import re

from careline.domain.rails.acute_concern import (
    MILD_RESOLVABLE_LABELS,
    check_acute_concern,
    resolved_transient,
    strip_denials,
    unsuppressed_text,
)
from careline.domain.rails.normalise import text_variants
from careline.domain.rails.red_flag import check_red_flag

#: Risk recorded on a symptom-report escalation (below the literal rail's 1.0
#: and the acute net's 0.9: structural evidence is broader and weaker).
SYMPTOM_REPORT_RISK: float = 0.85

# ---------------------------------------------------------------------------
# Subject reference
# ---------------------------------------------------------------------------
SUBJECT_PATTERNS: tuple[str, ...] = (
    # the caller
    r"\b(?:i|i'?m|im|i'?ve|ive|i'?d|i'?ll|me|my|mine|myself)\b",
    # someone they care for, by pronoun
    r"\b(?:he|she|him|her|his|hers|he'?s|she'?s|himself|herself|we|us|our)\b",
    # someone they care for, by role or age, without a possessive
    r"\b(?:the|this)\s+(?:baby|child|kid|toddler|infant)\b",
    r"\b\d{1,2}[\s-]+(?:year|yr|month)s?[\s-]+old\b",
    r"\b(?:mummy|mum|mom|mommy|mama|maa|papa|daddy|dad|nana|nani|nanna|dadi|granny|grandma|grandmother|grandad|grandpa|grandfather|husband|wife|partner|son|daughter|uncle|aunt|auntie|brother|sister)\b",
    # the caller's own post-op site ("the wound is …", "the dressing is soaked")
    r"\b(?:the|this)\s+(?:wound|dressing|bandage|incision|stitches|scar|drain|cut|stoma|stoma\s+bag|catheter|catheter\s+bag|line)\b",
    r"\b(?:operation|surgery|surgical|op)\s+(?:site|wound|area)\b",
    # transliterated Hindi / Hinglish first/third person
    r"\b(?:mujhe|mujhko|mujh|mera|meri|mere|hamein|humein|humko|usko|unko|unhe|unhein)\b",
    # v5: an implicit subject — a body part in a present-progressive clause
    # ("sir chest is paining very much") is the caller's own report.
    r"\b(?:chest|head|stomach|belly|tummy|heart|arm|leg|back|throat|body|hand|face)\s+(?:is|are|keeps?|has\s+been|started)\s+(?:\w+\s+){0,2}?\w+ing\b",
)

# Framing that names "me" without making the caller the subject of a symptom
# ("tell me about seizures", "can you let me know…"); stripped before the
# subject check. These are also the general-knowledge patterns: a message
# whose only subject token sits inside one of them is not a report.
GENERAL_KNOWLEDGE_PATTERNS: tuple[str, ...] = (
    r"\b(?:can|could|would)\s+you\s+(?:please\s+)?(?:tell|explain\s+to|remind|send|show|give)\s+me\b",
    r"\b(?:tell|let|remind|show|give|send|call|text|email|help)\s+me\s+(?:about|know|more|what|how|if|whether)\b",
    r"\bexplain\s+to\s+me\b",
    r"\bwhat\s+(?:is|are|causes?|happens)\b",
    r"\bhow\s+(?:does|do|is|are)\b",
    r"\bis\s+it\s+normal\s+for\s+(?:people|someone|anyone|patients|everyone)\b",
    r"\bin\s+general\b|\bgenerally\b",
)

# ---------------------------------------------------------------------------
# Symptom / body lexicon
# ---------------------------------------------------------------------------
# RED-FLAG tier: a hit with a subject fires; mild qualifiers never soften it.
SYMPTOM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("chest", r"\b(?:pain\w*|ache|aching|hurt\w*|tight\w*|pressure|weight|crush\w*|squeez\w*|heav\w*|burning|discomfort|band)\b[^.]{0,30}\b(?:chest|breastbone|sternum|ribs|ribcage)\b|\b(?:chest|breastbone|sternum|ribs|ribcage)\b[^.]{0,30}\b(?:pain\w*|ache|aching|hurt\w*|tight\w*|pressure|weight|crush\w*|squeez\w*|heav\w*|burn\w*|discomfort)\b|\bbeing\s+crushed\b|\bcrushing\s+(?:pain|feeling|sensation|weight|pressure)\b|\belephant\s+(?:is\s+)?(?:sitting|standing)\s+on\b"),
    ("heart_distress", r"\bheart\b[^.]{0,30}\b(?:explod\w*|burst\w*|going\s+to\s+stop|stopp\w*|jump\w*\s+out)\b|\b(?:heart|pulse)\b[^.]{0,20}\b(?:racing|pounding|hammering|thumping|galloping)\b[^.]{0,60}\b(?:faint\w*|pass\w*\s+out|dizz\w*|collaps\w*|sweat\w*|breath\w*|chest)\b"),
    ("sweating", r"\b(?:drenched|soaked|pouring|dripping)\s+(?:in|with)\s+(?:sweat|perspiration)\b|\bcold\s+sweats?\b|\bclammy\b"),
    ("bleeding", r"\bbleed\w*|\bh(?:a)?emorrhag\w*|\bgush\w*|\bspurt\w*|\bclots?\b|\bpool\s+of\b|\b(?:filling|full|filled)\s+(?:up\s+)?with\s+(?:bright\s+)?(?:red|blood)\b|\bsoak(?:ed|ing)\s+(?:through|in)\b|\bred\s+(?:right\s+)?through\b|(?<!donate )(?<!donating )(?<!donated )(?<!give )(?<!giving )(?<!gave )\bblood\b(?!\s*-?\s*(?:tests?|pressure|sugars?|reports?|work|group|count|counts|levels?|readings?|results?|donation|donors?|camp|drive|draw|samples?|thinners?|bank|type|glucose|vessels?|cells?)\b)"),
    ("gi_bleed", r"\b(?:black|tarry|tar[\s-]like|maroon)\b[^.]{0,25}\b(?:stools?|poo\w*|motions?|faeces|feces)\b|\b(?:stools?|poo\w*|motions?)\b[^.]{0,30}\b(?:black|tarry|like\s+tar|maroon)\b|\bcoffee[\s-]grounds?\b"),
    ("breathing", r"(?<!bad )(?<!deep )\bbreath\w*\b(?!\s+(?:exercises?|tests?|freshener|mints?))|\bwheez\w*|\bgasp\w*|\bchok\w*|\bsuffocat\w*|\b(?:short\s+of|out\s+of|gasping\s+for|struggling\s+for|fighting\s+for|can'?t\s+get\s+(?:any\s+|enough\s+)?)\s*air\b|\bgurgl\w*|\brattl\w*\s+(?:breath|chest|noise)|\bribs\s+(?:are\s+)?suck\w*\s+in\b|\bsaa?ns\b"),
    ("cyanosis", r"\b(?:lips?|face|fingers?|fingertips|nails?|skin|tongue)\b[^.]{0,20}\b(?:blue|bluish|grey|gray|greyish|grayish|purple|ashen)\b"),
    ("consciousness", r"\bfaint(?:ed|ing|s)?\b(?!\s+(?:rash|line|mark|smell|taste|sound|scar|red|pink))|\bpass(?:ed|ing|es)?\s+out\b|\bcollaps\w*|\bslump\w*|\bkeel\w*\s+over\b|\bblack(?:ed|ing)\s+out\b|\bwon'?t\s+wake\b|\bcan'?t\s+(?:keep\s+\w+\s+awake|wake\s+(?:him|her|them|me|up))\b|\bnodding\s+off\b|\b(?:can'?t|cannot|couldn'?t|unable\s+to|hard\s+to)\s+(?:rouse|wake|stir)\b|\bunresponsive\b|\bunconscious\b|\b(?:not|isn'?t|is\s+not|won'?t|doesn'?t|does\s+not|can'?t|stopped)\s+(?:respond\w*|answer\w*|react\w*)\b|\bout\s+of\s+it\b|\b(?:not|isn'?t|stopped)\s+(?:moving|getting\s+up|waking)\b|\blying\s+(?:on\s+the\s+)?(?:ground|floor)\b|\bstopped\s+making\s+sense\b|\b(?:doesn'?t|does\s+not|didn'?t)\s+(?:know|recogni[sz]e)\s+(?:where|who|me|us|her|him)\b|\b(?:talking|speaking)\s+(?:gibberish|nonsense|rubbish)\b|\bgibberish\b|\bnot\s+making\s+(?:any\s+)?sense\b|\b(?:everything|it\s+all|the\s+world)\s+went\s+(?:black|dark|white)\b|\b(?:i'?m|i\s+am|he'?s|she'?s|lying|found\s+\w+)\s+on\s+the\s+floor\b|\b(?:can'?t|cannot|can\s+barely|can\s+hardly|unable\s+to)\s+(?:stand(?:\s+up)?|walk|get\s+up|stay\s+awake|keep\s+awake)\b(?!\s+(?:the|it|this|that)\b)|\bbehosh\w*|\b(?:is|was|seems?|looks?|became|becoming|getting|gone|got|went)\s+(?:very\s+|really\s+|so\s+|suddenly\s+|all\s+)?(?:confused|disoriented|delirious)\b(?!\s+(?:about|by|with|over|regarding|whether|which|what|how|why|when))|\bconfusion\b"),
    ("neuro", r"\bnumb\w*|\bparaly\w*|\bdroop\w*|\bslurr\w*|\bgarbled\b|\b(?:seeing|see|saw)\s+double\b|\bdouble\s+vision\b|\b(?:vision|eyesight|sight)\b[^.]{0,20}\b(?:went|gone|going|lost|black|double|dark)\b|\b(?:lost|losing|loss\s+of)\s+(?:my\s+|his\s+|her\s+)?(?:vision|sight|speech|balance)\b|\b(?:can'?t|cannot|couldn'?t|unable\s+to)\s+(?:move|feel|lift|raise|grip|use)\s+(?:my\s+|his\s+|her\s+|the\s+)?(?:left\s+|right\s+)?(?:arms?|legs?|hands?|foot|feet|face|side|fingers?|body)\b|\b(?:went|gone|goes|going|feels?|felt)\s+(?:all\s+)?(?:heavy|dead|floppy|limp)\b|\b(?:side|arm|leg|face|hand)\b[^.]{0,15}\b(?:heavy|limp|floppy|dangl\w*)\b|\bfloppy\b|\b(?:legs?|arms?)\s+(?:won'?t|wouldn'?t|don'?t|doesn'?t|can'?t)\s+(?:work|move|hold)\b|\blopsided\b|\b(?:mouth|face|smile|lip)\b[^.]{0,20}\b(?:twisted|crooked|uneven|to\s+one\s+side)\b|\b(?:speech|words|talking|voice)\b[^.]{0,20}\b(?:muddled|jumbled|mixed\s+up|confused|strange|weird)\b|\b(?:can'?t|cannot|couldn'?t)\s+see\s+out\s+of\b|\bdropping\s+things\b|\b(?:face|arm|leg|hand|side|mouth|eye)\b[^.]{0,10}\b(?:won'?t|wouldn'?t|can'?t|doesn'?t)\s+move\b|\b(?:legs?|knees?)\s+(?:gave|give|giving)\s+way\b|\b(?:can'?t|cannot|couldn'?t)\s+feel\s+(?:them|it|anything)\b"),
    ("seizure", r"\bseizur\w*|\bconvuls\w*|\bjerk(?:ing|ed|s)\b|\b(?:had|having|has|have|in|threw|getting|get|gets|got)\s+(?:a\s+|another\s+|some\s+(?:kind|sort)\s+of\s+|what\s+looked\s+like\s+a\s+)?fits?\b|\bfitting\b|\beyes\s+(?:rolled|rolling)\s+(?:back|up)\b|\b(?:went|gone|go(?:es|ing)|turned)\s+(?:all\s+)?(?:stiff|rigid)\b|\bdaur[ae]\b|\bmirgi\b"),
    ("face_swelling", r"\b(?:lips?|eyes?|eyelids?|tongue|throat|face|mouth)\b[^.]{0,25}\b(?:swell\w*|swollen|blew\s+up|blown\s+up|balloon\w*|clos\w*\s+up)\b|\b(?:swell\w*|swollen)\s+(?:up\s+)?(?:of\s+)?(?:my\s+|his\s+|her\s+|the\s+)?(?:lips?|eyes?|eyelids?|tongue|throat|face)\b"),
    ("fever_rash", r"\b(?:fever\w*|temperature|burning\s+up|bukhar)\b[^.]{0,40}\b(?:rash\w*|spots|stiff\s+neck|purple)\b|\b(?:rash\w*|stiff\s+neck)\b[^.]{0,40}\b(?:fever\w*|temperature|burning\s+up|bukhar)\b"),
    ("pregnancy", r"\b(?:pregnan\w*|\d{1,2}\s+weeks|expecting)\b[^.]{0,40}\b(?:bleed\w*|blood|spotting|not\s+moving|no\s+movements?|isn'?t\s+moving|stopped\s+moving|waters?\s+(?:broke|broken))\b|\b(?:bleed\w*|spotting|not\s+moving|isn'?t\s+moving|stopped\s+moving|no\s+movements?)\b[^.]{0,40}\b(?:pregnan\w*|\d{1,2}\s+weeks)\b"),
    ("wound_open", r"\b(?:wound|incision|stitches|scar|cut|stoma|staples?)\b[^.]{0,25}\b(?:split|burst|opened|open\s+up|gaping|came\s+apart|come\s+apart|pulled\s+apart|see\s+inside)\b"),
    ("headache_red_flag", r"\b(?:worst|sudden\w*|thunderclap|exploding|splitting|pounding|severe|blinding)\b[^.]{0,30}\b(?:head\w*|migraine)\b[^.]{0,60}\b(?:light|neck|vomit\w*|sick|pregnan\w*|weeks|vision|flashing|confus\w*|seeing)\b|\b(?:head\w*)\b[^.]{0,20}\b(?:worst|never\s+had)\b|\b(?:pregnan\w*|\d{1,2}\s+weeks)\b[^.]{0,60}\b(?:headache|flashing\s+lights|blurr\w*\s+vision|swollen\s+(?:face|hands))\b"),
    ("cardiac_radiation", r"\b(?:pain\w*|ach\w*|heav\w*)\b[^.]{0,25}\b(?:jaw\b[^.]{0,15}\barm|arm\b[^.]{0,15}\bjaw)\b"),
    ("no_urine", r"\b(?:haven'?t|have\s+not|hasn'?t|has\s+not|not|no)\s+(?:peed|wee'?d|weed|urinated|passed\s+(?:any\s+)?urine|passing\s+urine)\b"),
    ("tearing_pain", r"\b(?:tearing|ripping|stabbing|sharp)\b[^.]{0,20}\bpain\b[^.]{0,30}\b(?:back|through|between\s+(?:my|the)\s+shoulder)"),
    ("infant_unwell", r"\b(?:baby|infant|newborn)\b[^.]{0,40}\b(?:floppy|won'?t\s+(?:feed|wake)|not\s+(?:feeding|waking)|refus\w*\s+(?:to\s+)?feed|temperature|fever\w*|rash|blue|limp|grunting)\b"),
    ("ingestion_quantity", r"\b(?:swallow\w*|drank|drunk|gulp\w*|downed|popped|chugged|overdos\w*|took|taken|ate|eaten|consumed)\b[^.]{0,25}\b(?:whole|entire|handful|bottle|strip|pack\w*|box|a\s+lot\s+of|lots\s+of|loads\s+of|a\s+load\s+of|a\s+bunch\s+of|half\s+(?:a|the)|dozens?|the\s+rest\s+of|too\s+many|so\s+many|many|every|all\s+(?:of\s+)?(?:the|my|his|her|their|those|these)?)\b[^.]{0,30}\b(?:tablets?|pills?|capsules?|tabs|caps|meds|medicines?|medication|painkillers?|sleeping|sleepers|paracetamol|ibuprofen|insulin|syrup|doses?|anti\w+|\w+(?:pam|zepam|pine|line|mol|fen|cin|ide|one|ate))\b(?![^.]{0,20}\b(?:as\s+(?:prescribed|advised|directed)|on\s+time|correctly|regularly)\b)"),
    ("ingestion_substance", r"\b(?:swallow\w*|drank|drunk|gulp\w*|ate|eaten|chew\w*|licked|got\s+into)\b[^.]{0,30}\b(?:bleach|cleaner|detergent|kerosene|petrol|diesel|batter(?:y|ies)|magnets?|acid|pesticide|insecticide|poison\w*|rat\s+\w+|antifreeze|phenyl|sanitis\w*|sanitiz\w*|nail\s+polish|mothballs?|naphthalene|camphor|lighter\s+fluid|paint|glue|washing\s+(?:pods?|powder|liquid|up\s+liquid)|dishwasher\s+\w+|laundry\s+\w+|cleaning\s+\w+|chemicals?|button\s+batter(?:y|ies)|e-?cig\w*|vape\s+liquid)\b|\bgot\s+into\b[^.]{0,30}\b(?:tablets?|pills?|medicine\w*|meds|bottle|cupboard|cabinet)\b"),
    ("ideation", r"\b(?:want(?:ed)?|wanna|going|plan\w*|ready|wish)\s+(?:to\s+)?(?:die|be\s+dead|end\s+it|disappear\s+forever)\b|\bkill\w*\s+my\s*self\b|\bend(?:ing)?\s+(?:my\s+(?:own\s+)?life|it\s+all)\b|\b(?:hurt|harm|cut)\w*\s+my\s*self\b|\bself[\s-]?harm\w*|\bsuicid\w*|\bno\s+(?:reason|point)\s+(?:to|in)\s+(?:live|living|going\s+on)\b|\bbetter\s+off\s+dead\b"),
    ("hinglish_pain", r"\b(?:seene?|chhati|chaati)\b[^.]{0,20}\b(?:dard|dabav|dabaav|jalan|bhaari|bhari)\b|\b(?:khoon|khun)\b"),
)

# SOFT tier: fires with a subject unless a mild qualifier sits right before it.
SOFT_SYMPTOM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("dizziness", r"\bdizz\w*|\bwoozy\b|\blight[\s-]?headed\w*|\bchakkar\b|\broom\s+(?:is\s+|keeps\s+)?(?:spinning|tilting|moving|swaying)\b"),
    ("drowsiness", r"\bdrows\w*|\bcan'?t\s+keep\s+(?:my\s+)?eyes\s+open\b"),
    ("weakness", r"\bweak(?:ness)?\b(?!\s+(?:tea|coffee|signal|wifi|password|point))"),
    ("vomiting", r"\bvomit\w*|\bthrowing\s+up\b|\bthrew\s+up\b|\bthrow\s+up\b|\bpuk\w*|\bult(?:i|ee)\b|\bbeing\s+sick\b|\b(?:bringing|brought|bring)\s+up\b|\bcan'?t\s+keep\s+(?:anything|any\s+food|food|water|fluids?)\s+down\b|\bspew\w*"),
    ("tingling", r"\btingl\w*|\bpins\s+and\s+needles\b"),
    ("rigors", r"\bshiver\w*|\brigou?rs\b|\bshaking\s+(?:chills|all\s+over)\b"),
    ("wound_leak", r"\b(?:wound|incision|stitches|scar|dressing|bandage|drain)\b[^.]{0,30}\b(?:drip\w*|seep\w*|ooz\w*|pus|smell\w*|stink\w*|hot|red\s+streak\w*)\b"),
)

MILD_QUALIFIERS: tuple[str, ...] = (
    r"a\s+(?:little\s+)?bit(?:\s+of)?", r"a\s+little", r"little", r"slight(?:ly)?",
    r"mild(?:ly)?", r"somewhat", r"kind\s+of", r"kinda", r"sort\s+of", r"bit",
    r"halka", r"thoda", r"thodi",
)

_SUBJECT_RE = re.compile("|".join(f"(?:{p})" for p in SUBJECT_PATTERNS), re.IGNORECASE)
_GENERAL_RE = re.compile("|".join(f"(?:{p})" for p in GENERAL_KNOWLEDGE_PATTERNS), re.IGNORECASE)
_SYMPTOM_RES = tuple((label, re.compile(p, re.IGNORECASE)) for label, p in SYMPTOM_PATTERNS)
_SOFT_RES = tuple((label, re.compile(p, re.IGNORECASE)) for label, p in SOFT_SYMPTOM_PATTERNS)
# A mild qualifier ending right before the symptom (optionally one word between:
# "a bit more dizzy", "slightly light-headed").
_MILD_BEFORE_RE = re.compile(
    r"\b(?:" + "|".join(MILD_QUALIFIERS) + r")\s+(?:\w+\s+)?$", re.IGNORECASE
)
_CURLY = str.maketrans({"’": "'", "‘": "'"})


def _has_subject(text: str) -> bool:
    return bool(_SUBJECT_RE.search(_GENERAL_RE.sub(" ", text)))


def _scan(text: str, *, resolved: bool = False) -> str | None:
    for label, rx in _SYMPTOM_RES:
        if rx.search(text):
            return label
    for label, rx in _SOFT_RES:
        # v5: transient-and-resolved framing softens only the mild labels.
        if resolved and label in MILD_RESOLVABLE_LABELS:
            continue
        for m in rx.finditer(text):
            if not _MILD_BEFORE_RE.search(text[max(0, m.start() - 30) : m.start()]):
                return label
    return None


def check_symptom_report(
    question: str, *, soften_hypothetical: bool = True, context: bool = True
) -> str | None:
    """Return the symptom label if this reads as a CURRENT symptom report by
    the caller or someone they care for, else ``None``.

    ``soften_hypothetical=False`` keeps explicit-hypothetical clauses live;
    ``context=False`` disables history / hypothetical / animal-subject context
    and the denial strip (the danger-concept invariant's view).
    """
    if not question:
        return None
    for variant in text_variants(question.translate(_CURLY)):
        if context:
            live = unsuppressed_text(variant, hypothetical=soften_hypothetical)
            # Ideation is matched with only the explicitly denied phrase removed.
            live = strip_denials(live)
        else:
            live = variant
        if not live or not _has_subject(live):
            continue
        label = _scan(live, resolved=context and resolved_transient(variant))
        if label is not None:
            return label
    return None


def mentions_danger_concept(question: str) -> str | None:
    """Every deterministic net with ALL context guards off — "is there any
    danger concept in this message at all, suppressed or not?"

    Used by the gate chain as a final invariant: such a question may be
    redirected, clarified or escalated, but never ANSWERED.
    """
    if not question:
        return None
    hit = check_red_flag(question, context=False)
    if hit is not None:
        return hit
    hit = check_acute_concern(question, context=False)
    if hit is not None:
        return f"acute:{hit}"
    hit = check_symptom_report(question, context=False)
    if hit is not None:
        return f"symptom:{hit}"
    return None


__all__ = [
    "GENERAL_KNOWLEDGE_PATTERNS",
    "MILD_QUALIFIERS",
    "SOFT_SYMPTOM_PATTERNS",
    "SUBJECT_PATTERNS",
    "SYMPTOM_PATTERNS",
    "SYMPTOM_REPORT_RISK",
    "check_symptom_report",
    "mentions_danger_concept",
]
