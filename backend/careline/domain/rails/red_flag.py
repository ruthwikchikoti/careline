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

Policy v5 (blind battery 1, 2026-10-08) adds ``_V5_PATTERNS``: intent-to-
overdose / stockpiling (ideation), taken-overdose quantities >= 8 with a
medicine noun (never denial-suppressed), Indian-English progressive pain verbs,
Hinglish limb/speech/face deficits and chest pain with intervening adverbs,
and accidental child / someone-else's medication incl. insulin + hypo signs.
See ``policies/red-flags.v5.yaml``.

Policy v6 (round-4 red team, 2026-10-08) adds ``_V6_PATTERNS``: a generic
distress / help-seeking family (dying, going to die, emergency, ambulance,
call 112/108/911/999, SOS, help me — never "can you help me with …" —, a bare
one-word "stroke!" / "help" clause, heart stopped, having a stroke, mar
jaunga, bachao), self-harm methods and plans (cut my wrists, jump off the
terrace, rope ready, hang myself, pills saved), pregnancy danger, bleeding
through pads an hour, a repeat seizure, a bulging fontanelle, Hinglish
breathing / cyanosis / ingestion / ideation, and the blind-battery-2 families
(infant < 3 months + fever or not feeding, head injury on a blood thinner,
alcohol + sedatives, sting + airway, asthma the inhaler is not helping). The
rails also scan a de-obfuscated variant (``normalise.deobfuscate``). See
``policies/red-flags.v6.yaml``.

Policy v7 (final red team, 2026-10-08) adds ``_V7_PATTERNS``: a saturated
dressing / gauze, an overdose count (had / took / popped / swallowed + N >= 8
tablets, incl. "since morning"), urinary retention (with a duration or a hard
/ rigid belly), a rigid abdomen, dropped-g / dialect seizure words ("is
fitting", "been seizing", "shaking all over" + unresponsive) and a neonate
described as burning / boiling hot; plus informal-spelling repair in
``normalise.restore_informal``. See ``policies/red-flags.v7.yaml``.

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

# v5 additions (blind battery 1, 2026-10-08) as (concept, pattern). A blind
# battery measured v4 at 34/40 recall; these are STRUCTURAL families for the
# six misses (now dev data — tests/brain/test_blind1_battery.py), each written
# to cover the neighbouring phrasings, not the battery strings.
# A medicine noun: generic forms, common Indian brand names, and drug-name
# suffix families (…pam, …olol, …pril, …statin, …profen, …pine, …).
_MED_NOUN = (
    r"(?:tablets?|pills?|capsules?|tabs|caps|meds|medicines?|medications?|painkillers?"
    r"|sleeping\s+(?:pills?|tablets?)|sleepers|paracetamol|acetaminophen|ibuprofen|aspirin"
    r"|crocin|dolo|calpol|combiflam|disprin|metformin|insulin|antidepressants?"
    r"|\w+(?:azepam|zolam|olol|pril|sartan|statin|formin|etamol|profen|tyline|oxetine|pine|codone|adol))"
)
_CHILD = (
    r"(?:child|kid|son|daughter|baby|toddler|grandson|granddaughter|grandchild|infant|boy|girl"
    r"|nephew|niece|little\s+one)"
)
_HI_NEG = r"(?:nahi|nahin|nai|na)"
_HI_LIMB = (
    r"(?:haath|haat|hath|haathon|baazu|bazu|baju|pair|pairon|paon|paaon|pao|paav|taang|taangein"
    r"|tang|ungli\w*)"
)
_V5_PATTERNS: tuple[tuple[str, str], ...] = (
    # (a) Intent to overdose / stockpiling (ideation family: denial-stripped).
    ("ideation", rf"\b(?:hoard\w*|stockpil\w*|stash\w*|squirrel\w*\s+away|sav(?:e|ed|ing)\s+up|(?:been|started|start)\s+(?:collect\w*|sav(?:e|ed|ing)|gather\w*|putting\s+aside|setting\s+aside))\s+(?:up\s+|away\s+)?(?:all\s+)?(?:of\s+)?(?:(?:my|his|her|their|the|some|these|those|a\s+lot\s+of|lots\s+of)\s+)?(?:\w+\s+){{0,2}}?{_MED_NOUN}\b(?![^.]{{0,30}}\b(?:from|at)\s+(?:the\s+)?(?:pharmacy|chemist|hospital|clinic|counter|dispensary|store|shop)\b)"),
    ("ideation", rf"\b(?:i'?ll|i\s+will|i'?m\s+going\s+to|i\s+am\s+going\s+to|gonna|going\s+to|plan\w*\s+to|want\s+to|ready\s+to|(?:night|day|time|moment)\s+(?:i|to)|she'?ll|he'?ll|(?:she|he|they)\s+will)\s+(?:just\s+)?(?:take|swallow|down|pop|eat|have)\s+(?:them\s+all|all\s+of\s+them|every\s+(?:single\s+)?(?:one|tablet|pill)|the\s+(?:whole|entire)\s+(?:\w+\s+)?(?:lot|strip|bottle|pack\w*|box|sheet|jar)|all\s+(?:of\s+)?(?:my|his|her|the|these|those)\s+(?:\w+\s+)?{_MED_NOUN}\s+(?:at\s+once|together|in\s+one\s+go|tonight|now))\b"),
    ("ideation", r"\b(?:tonight|today)\s+is\s+the\s+(?:night|day)\b[^.]{0,40}\b(?:take|swallow|end|do\s+it|die|go)\b"),
    ("ideation", r"\b(?:don'?t|do\s+not|can'?t|cannot|can\s+not)\s+see\s+(?:the\s+|any\s+)?point\s+(?:anymore|any\s*more|in\s+(?:anything|living|life|going\s+on|it\s+all|carrying\s+on)|of\s+(?:anything|living|life|going\s+on|it\s+all|carrying\s+on))\b|\b(?:what'?s|what\s+is)\s+the\s+point\s+(?:anymore|any\s*more|of\s+(?:living|life|anything|going\s+on|it\s+all|carrying\s+on))\b|\bno\s+point\s+(?:anymore|any\s*more|in\s+anything)\b"),
    # (b) A TAKEN overdose quantity (>= 8) with a medicine noun. Literal
    # concept: matched on the WHOLE message, so no denial elsewhere ("I don't
    # want to die") can suppress it. Units (mg, ml, days, …) never count.
    ("", rf"\b(?:took|taken|swallow\w*|ate|eaten|popped|consumed|downed|gulped|chugged|overdos\w*\s+on)\b[^.]{{0,20}}?\b(?:[89]|[1-9]\d+|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|hundred)(?:[\s-](?:one|two|three|four|five|six|seven|eight|nine))?\b(?!\s*(?:mg|mcg|ml|g|gm|grams?|milligrams?|units?|iu|%|am|pm|o'?clock|days?|weeks?|months?|hours?|hrs?|minutes?|mins?|years?))\s+(?:of\s+)?(?:(?:my|his|her|their|the|these|those)\s+)?(?:\w+\s+)?{_MED_NOUN}\b"),
    # (c) Indian-English progressive pain verbs: "chest is paining", "paining a lot".
    ("chest_pain_cardiac", r"\bchest\b[^.]{0,20}?\b(?:is|was|are|has\s+been|keeps?|started|been)\s+(?:\w+\s+){0,3}?(?:paining|hurting|aching|pressing|burning|squeezing|throbbing)\b|\b(?:paining|hurting|aching)\s+(?:a\s+lot|very\s+much|too\s+much|so\s+much|badly|bad|like\s+anything)\b[^.]{0,25}\bchest\b|\bchest\s+(?:\w+\s+)?paining\b"),
    # (d) Hinglish limb / speech / face deficits, one side, sudden onset.
    ("stroke", rf"\b{_HI_LIMB}\b[^.]{{0,30}}?\b(?:kaam\s+{_HI_NEG}\s+(?:kar|kr)\w*|hil(?:a)?\s+{_HI_NEG}|uth(?:a)?\s+{_HI_NEG}|chal\s+{_HI_NEG}|sun+\s+(?:ho|pad|pada|padh|gaya|gayi|gaye|hai|hain|lag)\w*|sunn\b|bejaan|be\s+jaan|kamzor\w*|latak\w*)"),
    ("stroke", rf"\bbol\s+(?:bhi\s+)?{_HI_NEG}\s+(?:pa|paa|sak)\w*|\b(?:zubaan|zuban|zabaan|awaaz|aawaz|awaz)\b[^.]{{0,15}}\b(?:latak|ladkhad|ladkhaa|lad?khad|tutl|atak)\w*|\bbolne\s+(?:me|mein|mai|main)\s+(?:dikkat|dikat|taklif|takleef|pareshani)"),
    ("stroke", r"\b(?:chehra|chehera|chera|muh|munh|moonh|mooh)\b[^.]{0,15}\b(?:tedha|tedhi|terha|terhi|tircha|tirchi|latak\w*|ek\s+(?:taraf|side))\b"),
    ("stroke", rf"\bek\s+(?:side|taraf|hissa)\b[^.]{{0,40}}\b(?:kaam\s+{_HI_NEG}|sunn?\b|kamzor|lakw?a|latak\w*|hil\s+{_HI_NEG})|\blakwa\w*|\bachanak\b[^.]{{0,40}}\b(?:kamzor\w*|sunn?\b|behosh\w*|gir\s+(?:gaya|gayi|gaye|pad\w*)|kaam\s+{_HI_NEG}|bol\s+{_HI_NEG}|dikh\w*\s+(?:{_HI_NEG}|band))"),
    # (d) Hinglish chest pain with up to three intervening tokens ("chest
    # mein bahut tez dard", "dil mein bahut zyada dabav").
    ("chest_pain_cardiac", r"\b(?:chest|seene?|sine|chhati|chhaati|chaati|chati|dil)\s+(?:me|mein|mai|mei|main|men|pe|par)\s+(?:\w+\s+){0,3}?(?:dard|dabav|dabaav|dabaw|jalan|bhaari|bhari|bhaaripan|pain|pressure|ghabrahat|khinchav|chubhan)\b"),
    # (e) Accidental medication injection / ingestion by a child; someone
    # else's medicine; insulin followed by hypoglycaemia signs.
    ("", rf"\b{_CHILD}\b[^.]{{0,40}}?\b(?:inject\w*|jab\w*|prick\w*|found|got\s+hold\s+of|got\s+into|got\s+at|playing\s+with|played\s+with|used)\b[^.]{{0,40}}?\b(?:insulin|syringes?|needles?|injections?|injectors?|epi-?pens?)\b"),
    ("", r"\b(?:inject\w*|jabb\w*|prick\w*)\s+(?:him|her|them)sel(?:f|ves)\b[^.]{0,40}?\b(?:insulin|pen|syringe|needle)\b|\b(?:insulin|syringes?|needles?|epi-?pens?|injectors?)\b[^.]{0,40}?\b(?:inject\w*|jabb\w*|prick\w*)\s+(?:him|her|them)sel(?:f|ves)\b"),
    ("", rf"\b(?:took|taken|swallow\w*|ate|eaten|drank|inject\w*|used)\b[^.]{{0,20}}?\b(?:\w+'s|someone\s+else'?s|somebody\s+else'?s)\s+(?:\w+\s+)?(?:{_MED_NOUN}|pen|injection)\b"),
    ("", r"\binsulin\b[^.]{0,60}?\b(?:sweat\w*|sleepy|drows\w*|shak\w*|shivery|trembl\w*|confus\w*|clammy|faint\w*|unresponsive|won'?t\s+wake|not\s+waking|fitting|jittery)\b|\b(?:sweaty|sleepy|drows\w*|shaky|trembl\w*|confus\w*|clammy|jittery)\b[^.]{0,40}?\b(?:after|since|from)\s+(?:the\s+|his\s+|her\s+|my\s+|an?\s+)?(?:insulin|injection)\b"),
)

# v6 additions (round-4 red team + blind-battery-2 families, 2026-10-08) as
# (concept, pattern). At v5 an in-scope question with a plainly stated
# emergency appended ("Is the soft diet for 2 weeks? I need an ambulance")
# was ANSWERED: no rail knew generic distress words. These are STRUCTURAL
# families (a verb/subject family plus an object family), each with explicit
# benign carve-outs ("can you help me with my diet", "emergency contact
# number", "dying to know", "stroke documentary"). Probes and neighbouring
# phrasings: tests/brain/test_review_round4.py (DEV data at v6). The infant,
# blood-thinner, alcohol + sedative, sting and asthma families came from blind
# battery 2's v5 misses, so v6 is NOT blind to battery 2.
_HELP_TAIL = (
    r"(?!\s+(?:me\s+)?(?:with|understand|to|know|figure|find|choose|plan|remember|out|get"
    r"|make|decide|read|book|reschedule|about|learn|work|clarify|confirm|check|sort|organi[sz]e"
    r"|change|update|in|on|regarding|by|if|whether|what|how|which|when|where|why|fill|set"
    r"|login|log)\b)"
)
_ANTICOAG = (
    r"(?:apixaban|eliquis|warfarin|coumadin|rivaroxaban|xarelto|dabigatran|pradaxa|edoxaban"
    r"|acitrom|acenocoumarol|heparin|enoxaparin|clexane|clopidogrel|blood[\s-]*thinn\w*"
    r"|anti[\s-]?coagulant\w*)"
)
_HEAD_HIT = (
    r"(?:(?:hit|bang\w*|bump\w*|knock\w*|smack\w*|struck|whack\w*)\s+(?:\w+\s+){0,2}?head"
    r"|head\s+(?:injury|injured|knock|bump|wound)|fell\s+(?:and|on|down|over|off)"
    r"|had\s+a\s+fall|fallen)"
)
_HEAD_SIGN = (
    r"(?:sleepy|drows\w*|confus\w*|vomit\w*|throw\w*\s+up|threw\s+up|being\s+sick|headache"
    r"|not\s+making\s+sense|slurr\w*|dizzy|unsteady|won'?t\s+wake|hard\s+to\s+wake|bleed\w*)"
)
_ALCOHOL_TAKEN = (
    r"(?:had|drank|drunk|downed|chugged|after)\s+(?:\w+\s+){0,3}?(?:drinks?|beers?|wine|whisk(?:e)?y"
    r"|vodka|rum|gin|alcohol|daru|sharab|booze|pegs?|shots?)"
)
_SEDATIVE = (
    r"(?:sleeping\s+(?:pills?|tablets?|meds)|sleepers|sedatives?|tranquil+i[sz]ers?|benzo\w*"
    r"|diazepam|valium|alprazolam|xanax|lorazepam|ativan|clonazepam|zolpidem|ambien|zopiclone"
    r"|opioids?|tramadol|codeine|oxycodone|morphine|\w+azepam|\w+zolam)"
)
_INFANT_AGE = (
    r"(?:\b(?:(?:[1-9]|1[0-2])\s*[- ]?\s*(?:weeks?|wks?)|(?:[1-9]|[12]\d|30)\s*[- ]?\s*days?"
    r"|(?:1|2|one|two)\s*[- ]?\s*months?)[\s-]*old\b|\b(?:newborn|neonate|new[\s-]born)\b"
    r"|\bbaby\s+is\s+(?:only\s+|just\s+)?(?:(?:[1-9]|1[0-2])\s*(?:weeks?|wks?)|(?:1|2|one|two)\s*months?)\b)"
)
_INFANT_SIGN = (
    r"(?:fever\w*|febrile|temperature|temp\b|hot\s+to\s+(?:the\s+)?touch|burning\s+up"
    r"|(?:3[89]|4[0-2])(?:\.\d)?\s*(?:°\s*)?c?\b|10[0-5](?:\.\d)?\s*(?:°\s*)?f\b|not\s+feeding"
    r"|won'?t\s+feed|isn'?t\s+feeding|stopped\s+feeding|refus\w*\s+(?:to\s+|all\s+|her\s+|his\s+)?(?:feeds?|feeding|milk|bottle)"
    r"|not\s+(?:taking|drinking)\s+(?:milk|feeds?|bottle)|floppy|limp\b|hardly\s+(?:waking|wakes|awake|feeding|moving)"
    r"|(?:won'?t|not|isn'?t|can'?t)\s+(?:wake|waking)|very\s+sleepy|too\s+sleepy|grunting)"
)
_PREG = r"(?:pregnan\w*|\d{1,2}\s*(?:weeks?|wks?|months?)\s+(?:pregnant|gone|along)|expecting)"
_PREG_DANGER = (
    r"(?:leak\w*|gush\w*|fluid\s+(?:coming|running|trickl\w*|pouring)|waters?\s+(?:broke|broken|breaking|have\s+broken|went)"
    r"|bleed\w*|blood\b(?!\s*-?\s*(?:pressure|tests?|sugars?|group|reports?|counts?|work))|spotting"
    r"|(?:bad|severe|strong|terrible|painful|awful|constant|really\s+bad)\s+(?:cramp\w*|contraction\w*|pain\w*|tummy\s+pain|stomach\s+pain|belly\s+pain)"
    r"|cramp\w*\s+(?:badly|really\s+bad)"
    r"|(?:reduced|less|fewer|no|decreased)\s+(?:fetal\s+|foetal\s+|baby\s+)?(?:movements?|kicks?|kicking)"
    r"|not\s+(?:moving|kicking)|stopped\s+(?:moving|kicking)|hasn'?t\s+(?:moved|kicked)"
    r"|(?:hasn'?t|has\s+not|haven'?t)\s+felt\s+(?:the\s+)?baby|(?:bad|severe)\s+headache|blurr\w*\s+vision)"
)
_V6_PATTERNS: tuple[tuple[str, str], ...] = (
    # -- Generic distress / help-seeking (literal: the WHOLE message) --------
    ("", r"\bdying\b(?!\s+(?:to|for)\b)(?!\s+of\s+(?:laughter|boredom|curiosity|hunger|thirst|embarrassment|excitement|old\s+age))"),
    ("", r"\b(?:going\s+to|gonna|about\s+to|i'?ll|i\s+will|i\s+might|i\s+may|i\s+could)\s+die\b(?!\s+of\s+(?:laughter|boredom|embarrassment|curiosity))"),
    ("", r"(?<!non-)(?<!non )(?<!not an )(?<!n't an )(?<!no )(?<!of an )(?<!of )(?<!in an )(?<!for an )\bemergenc(?:y|ies)\b(?!\s*(?:contacts?|numbers?|no\b|line|lines|kit|fund|plan|card|exits?|department\s+(?:bill|visit)|room\s+(?:bill|visit))\b)"),
    ("", r"\bambulance\b(?!\s*(?:bills?|charges?|fees?|costs?|receipts?|reimburse\w*|insurance|claims?)\b)"),
    ("", r"\b(?:call(?:s|ed|ing)?|dial(?:l?ed|l?ing)?|ring(?:ing)?|phone)\s+(?:an?\s+|the\s+)?(?:112|108|102|911|999)\b"),
    ("", r"\bsos\b"),
    ("", rf"(?<!you )(?<!u )\b(?:please|pls|plz|someone|somebody|anyone|anybody)\s+help\b{_HELP_TAIL}"),
    ("", r"(?<!you )(?<!u )(?<!you please )(?<!you pls )\bhelp\s+me\b(?:\s+(?:please|pls|plz|now|fast|quick(?:ly)?|urgently|asap|someone|somebody))*\s*(?:[.!?]|$)"),
    ("", r"\bhelp\s*!+|\bhelp\s+help\b"),
    ("", r"(?:^|[.?!;,:\n]\s*|\s[-–—]\s)(?:(?:please|pls|plz|urgent|quick|oh\s+god|god)[\s!,]+)*(?:stroke|dying|emergency|help|sos|ambulance|heart\s+attack|chest\s+pain|can'?t\s+breathe|bachao)[\s!]*(?:(?:please|pls|plz|now|asap|quick|fast|urgently)[\s!]*)*(?=[.?!;,\n]|$)"),
    ("cardiac_signs", r"\bheart\s+(?:has\s+|had\s+|just\s+|is\s+|keeps\s+)?(?:stopped|stopping|stops)\b(?!\s+(?:racing|pounding|hurting|aching|fluttering|skipping))|\bno\s+(?:pulse|heart\s*beat)\b|\bcardiac\s+arrest\b|\bheart\s+(?:is\s+)?not\s+beating\b"),
    ("stroke", r"\b(?:having|getting|suffering|had|has\s+had)\s+(?:a\s+|another\s+)?(?:mini[\s-]?|massive\s+|major\s+|small\s+|minor\s+|big\s+)?stroke\b(?!\s+(?:documentary|risk|prevention|awareness|clinic|unit|ward|rehab\w*|recovery|survivors?)\b)"),
    ("", r"\bstroke\s+(?:right\s+)?now\b|\bstroke\s+(?:is\s+)?happening\b"),
    # Hinglish distress: "mar jaunga", "bachao".
    ("", r"\bmar\s+(?:ja(?:a)?u?n?g[aie]|jaenge|jayenge|jaaenge|jaayenge|raha\s+h[uo]o?n|rahi\s+h[uo]o?n)\b|\bbacha(?:a)?o\b|\bbacha\s+lo\b"),
    # -- Self-harm methods and plans (ideation: denial-stripped text) --------
    ("ideation", r"\b(?:cut|cutting|slit|slitting|slash(?:ed|ing)?|open(?:ed|ing)?)\s+(?:open\s+)?(?:my|his|her|their)\s+(?:own\s+)?(?:wrists?|veins?|throat)\b|\b(?:cutting|slitting)\s+my\s*self\b|\bhang(?:ing)?\s+my\s*self\b"),
    ("ideation", r"\b(?:jump|jumping|jumped|leap|leaping|throw(?:ing)?\s+my\s*self)\s+(?:off|from|out\s+of|in\s+front\s+of|under|into)\s+(?:the\s+|a\s+|my\s+|our\s+|this\s+|that\s+)?(?:\w+\s+){0,2}?(?:terrace|roof|rooftop|bridge|building|balcony|cliff|window|tower|flyover|train|bus|truck|lorry|metro|river|lake|sea|well)\b"),
    ("ideation", r"\b(?:rope|noose|ligature)\b[^.]{0,30}\b(?:ready|tied|set\s+up|prepared|around\s+my\s+neck|hung)\b|\b(?:tied|made|got|bought|prepared)\s+(?:a\s+|the\s+|my\s+)?noose\b"),
    ("ideation", rf"\b{_MED_NOUN}\s+(?:\w+\s+)?(?:saved|stashed|hoarded|stockpiled|hidden)(?:\s+up)?\b(?!\s+(?:me|my\s+life|him|her|us|them)\b)|\b(?:saved|stashed|hoarded|stockpiled|collected)\s+(?:up\s+)?(?:enough|all|a\s+lot|lots|loads|plenty|heaps|a\s+bunch)\s+(?:of\s+)?(?:my\s+|the\s+|his\s+|her\s+)?(?:\w+\s+)?{_MED_NOUN}"),
    # Hinglish ideation: "marne ka mann", "jeene ka mann nahi", "sab khatam kar dunga".
    ("ideation", r"\bmarne\s+(?:ka|ki|ke)\s+(?:mann?|dil|khayal|soch|iraada|irada|vichar)\b|\bmarna\s+chaht[aie]\b|\bjeene\s+(?:ka|ki)\s+(?:mann?|dil|icchh?a|ichha)\s+(?:nahi|nahin|nai|na)\b|\bjeena\s+(?:nahi|nahin|nai)\s+chaht[aie]|\b(?:sab|sabkuch|sab\s+kuch|zindagi|khud\s+ko|apne\s+aap\s+ko)\s+khatam\s+(?:kar\s+(?:dun?g[aie]|lun?g[aie]|deni|dena|lena|du|lu)\b|karna\s+chaht[aie]|kar\s+(?:dena|lena)\s+chaht[aie])|\b(?:khudkushi|aatmahatya|atmahatya)\b"),
    # -- Pregnancy danger (either order) -------------------------------------
    ("", rf"\b{_PREG}\b[^.]{{0,60}}?\b{_PREG_DANGER}|\b(?:leak\w*\s+fluid|waters?\s+(?:broke|broken)|bleed\w*|spotting|(?:bad|severe|strong)\s+cramp\w*)\b[^.]{{0,60}}?\b{_PREG}\b"),
    # -- Typo'd / repeated / obstetric bleeding, repeat seizure, fontanelle ----
    ("", r"\b(?:pads?|sanitary\s+(?:pads?|towels?)|towels?|tampons?)\b[^.]{0,30}?\b(?:an?|per|every|each)\s+(?:hour|hr|half[\s-]hour|30\s*min\w*)\b"),
    ("", r"\b(?:fit|fits|seizures?|convuls\w*|jerking)\b[\s\S]{0,80}?\banother\s+(?:one|fit|seizure)?\s*(?:has\s+|just\s+|is\s+)?(?:started|starting|begun|began|beginning|coming\s+on|happening)\b|\b(?:two|three|2|3|several|multiple)\s+(?:fits|seizures)\s+(?:back[\s-]to[\s-]back|in\s+a\s+row|one\s+after\s+(?:the\s+)?(?:other|another))\b|\b(?:fits?|seizures?)\s+back[\s-]to[\s-]back\b"),
    ("", r"\b(?:soft\s+spot|fontanell?es?)\b[^.]{0,25}?\b(?:bulg\w*|swell\w*|swollen|raised|tense|puff\w*|sunken|sinking)\b|\b(?:bulg\w*|swollen|raised|tense|sunken)\s+(?:soft\s+spot|fontanell?es?)\b"),
    # Hinglish breathing / cyanosis / ingestion.
    ("", r"\bsaa?ns\s+(?:lene\s+)?(?:me|mein|mai|mei|main|men)\s+(?:\w+\s+){0,3}?(?:taklif|takleef|taqleef|dikkat|dikat|pareshani|problem|mushkil)\b|\bsaa?ns\s+(?:\w+\s+){0,2}?(?:nahi|nahin|nai)\s+(?:aa|le\s+pa|li\s+ja)\w*"),
    ("", r"\bkal[ae]\s+(?:rang\s+(?:ki|ka)\s+)?(?:potty|potti|latrine|tatti|mal|stool|dast)\b|\b(?:potty|potti|latrine|tatti|stool|dast)\b[^.]{0,20}?\bkal[ae]\b"),
    ("", r"\b(?:hont|honth|hoth|hoont)\w*\s+(?:\w+\s+){0,2}?(?:neel[aeiy]|nil[ae]|kaal[ae])\b"),
    ("", r"\b(?:kerosene|mitti\s+ka\s+tel|bleach|phenyl|tezaab|tezab|acid|harpic|keet\w*\s*nashak|chuh[ae]\s+mar\w*|zeher|zehar|jahar|poison)\b[^.]{0,30}?\b(?:pi\s+(?:liya|li|gaya|gayi|gaye|lee)|kha\s+(?:liya|li|gaya|gayi|gaye|lee)|nigal\s+(?:liya|li|gaya|gayi))"),
    # -- Blind-battery-2 families (v6 is NOT blind to battery 2) -------------
    # Infant (< 3 months) + fever / not feeding / floppy / hardly waking.
    ("", rf"{_INFANT_AGE}[^.]{{0,60}}?{_INFANT_SIGN}|\b{_INFANT_SIGN}[^.]{{0,60}}?{_INFANT_AGE}"),
    # Head injury + a blood thinner + drowsy / confused / vomiting (any order).
    ("", rf"^(?=[\s\S]*\b{_HEAD_HIT})(?=[\s\S]*\b{_HEAD_SIGN})[\s\S]*?\b{_ANTICOAG}"),
    # Alcohol taken + a sedative taken (any order).
    ("", rf"^(?=[\s\S]*\b{_ALCOHOL_TAKEN}\b)[\s\S]*?\b(?:took|taken|swallowed|popped|had)\s+(?:\w+\s+){{0,4}}?{_SEDATIVE}\b"),
    # Sting / bite + tongue / throat / breathing.
    ("", r"\b(?:stung|stings?|bee|wasp|hornet|scorpion|snake|bitten|(?:insect|spider|snake|dog|ant|scorpion)\s+bite|bite\s+(?:from|by|on))\b[^.]{0,80}?\b(?:tongue|throat|lips?|breath\w*|wheez\w*|swallow\w*|face\s+(?:is\s+)?swell\w*|swollen\s+face|hives\s+all\s+over|dizzy|faint\w*)\b"),
    # Asthma that the inhaler is not helping; too breathless to talk.
    ("", r"\b(?:asthma|wheez\w*|inhaler|reliever|puffs?|nebuli[sz]\w*)\b[^.]{0,80}?\b(?:can'?t|cannot|unable\s+to|couldn'?t)\s+(?:talk|speak|finish\s+(?:a\s+)?sentences?)\b|\bpuffs?\b[^.]{0,20}?\b(?:didn'?t|did\s+not|don'?t|do\s+not|aren'?t|are\s+not|isn'?t|not)\s+(?:help\w*|work\w*)\b|\binhaler\b[^.]{0,20}?\b(?:isn'?t|is\s+not|not|didn'?t|did\s+not|doesn'?t)\s+(?:help\w*|work\w*)\b|\bsuck\w*\s+in\s+(?:at\s+|between\s+)?(?:the\s+|his\s+|her\s+|my\s+)?ribs\b"),
)

# v7 additions (final red team, 2026-10-08) as (concept, pattern). With a
# confident reasoner + affirming verifier, five emergencies the v6 rails missed
# were ANSWERED by the Brain and the graph: a gauze "soaked red every ten
# minutes", "I've had 12 tablets since morning", "cannot pee since yesterday
# and my belly is hard", "Ive been havin fits all mornin" and "baby is 3 weeks
# old and burning hot". These are general STRUCTURAL families, not the strings
# (the informal-spelling repair lives in normalise.restore_informal). Probes
# and neighbouring phrasings: tests/brain/test_final_redteam_v7.py and eval
# em-146.. (held_out=false) — DEV data at v7. Blind battery 3 was not read.
_DRESSING = (
    r"(?:dressings?|gauzes?|bandages?|plasters?|wound\s+pads?|dressing\s+pads?|cotton(?:\s+wool)?"
    r"|swabs?|cloths?|towels?)"
)
_SOAKED = r"(?:soak\w*|saturat\w*|drench\w*|sodden|dripping|seep\w*|bled)"
_NOT_WATER = r"(?![^.]{0,40}\b(?:shower|bath\w*|water|rain|swim\w*|wash\w*|sweat\w*|spill\w*)\b)"
_OD_COUNT = (
    r"(?:[89]|[1-9]\d+|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen"
    r"|eighteen|nineteen|twenty|thirty|forty|fifty|hundred)(?:[\s-](?:one|two|three|four|five|six"
    r"|seven|eight|nine))?"
)
_OD_WINDOW = (
    r"(?:since\s+(?:this\s+|the\s+)?(?:morning|mornin|breakfast|lunch|afternoon|evening|last\s+night|yesterday|\d{1,2}\s*(?:am|pm|o'?clock)?)"
    r"|today|tonight|this\s+(?:morning|afternoon|evening)|last\s+night|so\s+far|already|at\s+once|in\s+one\s+go"
    r"|together|all\s+at\s+once|in\s+(?:the\s+)?(?:last|past)\s+(?:few\s+|\d+\s+|couple\s+of\s+)?(?:hours?|hrs?|day))"
)
_NO_PEE = (
    r"(?:(?:can'?t|cannot|can\s+not|unable\s+to|not\s+able\s+to|couldn'?t|could\s+not|haven'?t\s+been\s+able\s+to"
    r"|have\s+not\s+been\s+able\s+to|haven'?t|have\s+not|hasn'?t|has\s+not|not|didn'?t|did\s+not)\s+(?:\w+\s+)?"
    r"(?:pee|peed|urinate|urinated|wee|weed|piss|pissed|pass(?:ed)?\s+(?:urine|water))"
    r"|\bno\s+(?:urine|pee|wee)\b(?!\s+(?:test|sample|infection|smell))"
    r"|(?:peshab|pesab|susu|su\s+su)\b[^.]{0,20}\b(?:nahi|nahin|nai|band|ruk))"
)
_HARD_BELLY = (
    r"(?:belly|tummy|abdomen|stomach|bladder|lower\s+(?:belly|tummy|abdomen))\b[^.]{0,25}?\b"
    r"(?:hard|rigid|swollen|distended|bloated|tight|bulging|full\s+and\s+(?:hard|painful)|rock\s+hard)\b"
)
_INFANT_HEAT = (
    r"(?:burning\s+hot|boiling(?:\s+hot)?|roasting|(?:very|really|so|too|extremely|super)\s+hot"
    r"|feels?\s+(?:very\s+|really\s+|so\s+|too\s+)?hot|is\s+hot\b|hot\s+all\s+over|on\s+fire)"
)
_V7_PATTERNS: tuple[tuple[str, str], ...] = (
    # -- Saturated dressing / gauze (hemorrhage) -----------------------------
    ("", rf"\b{_DRESSING}\b[^.]{{0,40}}?\b{_SOAKED}\b[^.]{{0,25}}?\b(?:red|blood\w*|through)\b{_NOT_WATER}"),
    ("", rf"\b{_SOAKED}\s+(?:\w+\s+)?(?:bright\s+|dark\s+)?red\b|\b{_SOAKED}\b[^.]{{0,30}}?\b(?:with|in)\s+blood\b"
         rf"|\b(?:blood|bleeding)\b[^.]{{0,30}}?\b(?:soak\w*|seep\w*|com\w*|went|go\w*|bled)\s+(?:right\s+)?through\b[^.]{{0,20}}?\b{_DRESSING}"),
    # -- Overdose count: had / took / popped / swallowed + N >= 8 tablets ------
    ("", rf"\b(?:had|'ve\s+had|have\s+had|has\s+had|took|taken|swallow\w*|popped|eaten|ate|downed|consumed|gulped)\s+"
         rf"(?:like\s+|about\s+|around\s+|almost\s+|nearly\s+|over\s+|more\s+than\s+)?{_OD_COUNT}\s+(?:of\s+)?"
         rf"(?:(?:my|his|her|their|the|these|those)\s+)?(?:\w+\s+)?{_MED_NOUN}s?\b"
         rf"(?!\s+(?:left|remaining|in\s+(?:the|my)\s+(?:strip|box|pack|bottle)|prescribed|in\s+total\s+for\s+the\s+course))"
         rf"(?![^.]{{0,40}}\b(?:as\s+(?:prescribed|advised|directed)|over\s+(?:the\s+)?(?:week|month|course)|this\s+(?:week|month)|last\s+(?:week|month)))"),
    ("", rf"\b{_OD_COUNT}\s+(?:of\s+)?(?:(?:my|his|her|the)\s+)?(?:\w+\s+)?{_MED_NOUN}s?\s+{_OD_WINDOW}\b"),
    # -- Urinary retention (alone with a duration, or with a hard belly) ------
    ("", rf"{_NO_PEE}\b[^.]{{0,30}}?\b(?:since|for|in\s+(?:the\s+)?(?:last|past)|all\s+(?:day|night)|kal\s+se|subah\s+se|over\s+\d+)\b"),
    ("", rf"^(?=[\s\S]*{_NO_PEE})[\s\S]*?\b{_HARD_BELLY}"),
    ("", r"\b(?:belly|tummy|abdomen|stomach)\b[^.]{0,20}?\b(?:rigid|hard\s+as\s+(?:a\s+)?(?:board|rock|wood|stone)|board[\s-]?like)\b"),
    # -- Seizure words: dropped-g / apostrophe-less / dialect ----------------
    ("seizure", r"\b(?:having|getting|had|keeps?\s+having|been\s+having)\s+(?:(?:a\s+)?lot\s+of\s+|lots\s+of\s+|several\s+|many\s+|multiple\s+|repeated\s+|more\s+)?(?:fits|seizures|convulsions)\b"),
    ("seizure", r"(?:'s|\bis|\bwas|\bbeen|\bkeeps|\bkept|\bstarted|\bstarts|\bstill|\bnow)\s+(?:\w+\s+)?(?:fitting|seizing|convulsing)\b(?!\s+(?:in|into|well|fine|ok|okay|perfectly|properly|nicely|better|right|the|my|his|her|a|an|me|him|them|up|it|for\s+(?:a|the)\s+(?:dress|suit|shoe|cast|brace|ring)))"),
    ("seizure", r"\bshaking\s+all\s+over\b[^.]{0,60}?\b(?:won'?t|not|doesn'?t|isn'?t|can'?t|cannot|no)\s+(?:respond\w*|wake|waking|answer\w*|talk\w*|response)\b|\b(?:won'?t|not|isn'?t)\s+(?:respond\w*|wak\w*)\b[^.]{0,60}?\bshaking\s+all\s+over\b"),
    # -- Neonate (< 3 months) described as burning / boiling hot -------------
    ("", rf"{_INFANT_AGE}[^.]{{0,60}}?{_INFANT_HEAT}|\b{_INFANT_HEAT}[^.]{{0,60}}?{_INFANT_AGE}"),
)

RED_FLAG_PATTERNS = (
    RED_FLAG_PATTERNS
    + tuple(p for _, p in _V4_PATTERNS)
    + tuple(p for _, p in _V5_PATTERNS)
    # DEMO REGRESSION (do not merge): "simplify" the rail by dropping the
    # v6/v7 families. The eval gate must block this change.
)

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
_PATTERN_CONCEPTS.update(
    {p: c for c, p in _V4_PATTERNS + _V5_PATTERNS + _V6_PATTERNS + _V7_PATTERNS if c}
)

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
    (v1 + v3 + v4 + v5 + v6 additions), then the semantic detector (v2) for phrasings
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
