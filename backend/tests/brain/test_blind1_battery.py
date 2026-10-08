"""Blind battery 1 — the misses and false escalations of policy v4 (DEV data at v5).

Provenance, stated plainly:

* A blind battery (40 emergencies + 40 benign, written by an agent that had
  never seen the code) measured policy v4 at 34/40 emergency recall and 4/40
  false escalations. The ten failures are pinned VERBATIM below.
* They DROVE the policy v5 mechanisms, so from v5 on they are DEV data: a fit,
  not generalisation evidence. The ``_GENERALISE_*`` probes beside each
  mechanism are neighbouring phrasings written alongside the fix (also dev
  data) — they guard against string-pasting the battery items, nothing more.
  Generalisation must be measured on a fresh blind battery.

Mechanisms (v5):

(a) intent-to-overdose / stockpiling ("saving up my pills", "take them all",
    "tonight is the night", "don't see the point anymore") — ideation family,
    matched on the denial-stripped text;
(b) a TAKEN overdose quantity (>= 8, "like 30") with a medicine noun (incl.
    drug names) escalates whatever denial appears elsewhere — a denial never
    suppresses an ingestion;
(c) Indian-English progressive pain verbs ("chest is paining", "paining a
    lot"), with an implicit subject (a body part in a present-progressive
    clause is the caller's own report);
(d) Hinglish limb/speech/face deficits (haath, pair, kaam nahi kar raha, sun/
    sunn, bol nahi pa rahi, chehra tedha, achanak, ek side) and "chest/seene
    mein ... dard" with up to three intervening tokens;
(e) accidental injection/ingestion of medication by a child, someone else's
    medicine, and insulin + hypoglycaemia signs;
(f) precision: media/fiction/third-party framing ("watched a movie where
    someone had a heart attack") is stripped as a span (never a whole clause:
    "... and since then my chest hurts" stays live); transient-and-resolved
    framing ("passes in a second", "it's gone now", "playing normally")
    suppresses only MILD labels (dizziness, fever), never a red flag; "donate
    blood" is not bleeding.
"""

from __future__ import annotations

import pytest

from careline.domain.enums import Verdict
from careline.domain.rails.acute_concern import check_acute_concern, unsuppressed_text
from careline.domain.rails.red_flag import check_red_flag
from careline.domain.rails.symptom_report import check_symptom_report, mentions_danger_concept
from tests.brain.test_review_round2 import _both, _parity

# -- The six v4 misses (verbatim; DEV data at v5) ---------------------------
BLIND1_MISSES = [
    "honestly I don't see the point anymore. I've been saving up my pills and I think tonight is the night I take them all",
    "sir chest is paining very much since half hour, sweating also, what to do",
    "Mummy ka ek side ka haath aur pair kaam nahi kar raha achanak se, bol bhi nahi pa rahi",
    "i took like 30 paracetamol an hour ago, i dont want to die anymore but i feel sick",
    "Mujhe chest mein bahut tez dard ho raha hai aur left haath sun ho gaya hai",
    "my kid found grandma's insulin pen and injected himself, he's now sweaty and sleepy",
]

# -- The four v4 false escalations (verbatim; DEV data at v5) ---------------
BLIND1_FALSE_ESCALATIONS = [
    "I watched a movie where someone had a heart attack, what are the warning signs I should watch out for?",
    "I sometimes feel a bit dizzy when I get up quickly from bed, it passes in a second",
    "My son had a fever two days ago but it's gone now and he's playing normally",
    "Can I donate blood while on these medications?",
]


@pytest.mark.parametrize("question", BLIND1_MISSES, ids=lambda q: q[:48])
def test_blind1_misses_escalate_in_brain_and_graph(question):
    b, g = _both(question)
    assert b.verdict is Verdict.ESCALATE, f"missed emergency: {question!r} -> {b.verdict}"
    _parity(b, g)


@pytest.mark.parametrize("question", BLIND1_FALSE_ESCALATIONS, ids=lambda q: q[:48])
def test_blind1_false_escalations_no_longer_escalate(question):
    b, g = _both(question)
    assert b.verdict is not Verdict.ESCALATE, f"over-escalation: {question!r}"
    _parity(b, g)


# ---------------------------------------------------------------------------
# (a) intent-to-overdose / stockpiling
# ---------------------------------------------------------------------------
_GENERALISE_A = [
    "I have been collecting my sleeping tablets for weeks",
    "I've been stockpiling my tablets in a drawer",
    "she has been hoarding her pills, she says she will take them all",
    "I'm going to swallow all of them tonight",
    "what's the point of living anymore",
    "I can't see the point anymore",
    "today is the day I take the whole strip",
]


@pytest.mark.parametrize("question", _GENERALISE_A, ids=lambda q: q[:48])
def test_a_intent_to_overdose_escalates(question):
    assert check_red_flag(question) is not None, question


def test_a_a_denial_elsewhere_does_not_silence_stockpiling():
    q = "I would never hurt myself, but I've been saving up my pills"
    assert check_red_flag(q) is not None


@pytest.mark.parametrize(
    "question",
    [
        "Can I collect my tablets from the pharmacy tomorrow?",
        "Do I take them all at once or spread out over the day?",
        "Where should I store my insulin pens?",
        "I don't see the point of the second blood test, can you explain?",
    ],
)
def test_a_routine_medication_logistics_do_not_fire_the_ideation_rail(question):
    assert check_red_flag(question) is None, question


# ---------------------------------------------------------------------------
# (b) taken-overdose quantity — a denial never suppresses an ingestion
# ---------------------------------------------------------------------------
_GENERALISE_B = [
    "I took 8 paracetamol this morning",
    "she swallowed about 20 crocin",
    "my husband downed like 15 of his sleeping pills",
    "took around 12 ibuprofen an hour ago",
    "I swallowed nine tablets of metformin",
]


@pytest.mark.parametrize("question", _GENERALISE_B, ids=lambda q: q[:48])
def test_b_overdose_quantity_with_a_medicine_noun_escalates(question):
    assert check_red_flag(question) is not None, question


@pytest.mark.parametrize(
    "question",
    [
        "I don't want to die, I took 30 paracetamol",
        "i'm not suicidal but i popped like 25 of my pills",
        "I would never harm myself, I just took 10 dolo to sleep",
    ],
)
def test_b_denial_never_suppresses_an_ingestion(question):
    assert check_red_flag(question) is not None, question


@pytest.mark.parametrize(
    "question",
    [
        "I took 2 paracetamol for the pain",
        "I take 500 mg paracetamol twice a day",
        "I took my tablets at 8 this morning",
    ],
)
def test_b_ordinary_doses_do_not_fire(question):
    assert check_red_flag(question) is None, question


# ---------------------------------------------------------------------------
# (c) Indian-English progressive symptom verbs
# ---------------------------------------------------------------------------
_GENERALISE_C = [
    "chest is paining since morning",
    "my chest is hurting a lot",
    "chest paining a lot after climbing stairs",
    "doctor, chest is very much paining and left arm also",
]


@pytest.mark.parametrize("question", _GENERALISE_C, ids=lambda q: q[:48])
def test_c_progressive_pain_verbs_escalate(question):
    b, _ = _both(question)
    assert b.verdict is Verdict.ESCALATE, question


# ---------------------------------------------------------------------------
# (d) Hinglish deficits and chest pain with intervening adverbs
# ---------------------------------------------------------------------------
_GENERALISE_D = [
    "papa ka haath achanak se kaam nahi kar raha",
    "dadi ka pair sunn ho gaya hai",
    "unka chehra tedha ho gaya hai",
    "wo bol nahi pa rahe achanak",
    "seene mein bahut zor se dard ho raha hai",
    "chest mein tez dard hai",
    "dil mein bahut zyada dabav lag raha hai",
]


@pytest.mark.parametrize("question", _GENERALISE_D, ids=lambda q: q[:48])
def test_d_hinglish_deficits_and_chest_pain_escalate(question):
    assert check_red_flag(question) is not None, question


@pytest.mark.parametrize(
    "question",
    [
        "I bought a new pair of shoes for my walks, is that fine?",
        "Can I sit in the sun after the operation?",
    ],
)
def test_d_english_homographs_do_not_fire(question):
    assert check_red_flag(question) is None, question


# ---------------------------------------------------------------------------
# (e) accidental medication injection / ingestion
# ---------------------------------------------------------------------------
_GENERALISE_E = [
    "my daughter got hold of my insulin pen and jabbed herself",
    "the toddler was playing with a syringe of insulin and injected himself",
    "my son swallowed some of his grandpa's tablets",
    "I took my husband's insulin by mistake and now I'm shaky",
    "after the insulin he is very sleepy and confused",
]


@pytest.mark.parametrize("question", _GENERALISE_E, ids=lambda q: q[:48])
def test_e_accidental_medication_and_hypo_signs_escalate(question):
    assert check_red_flag(question) is not None, question


# ---------------------------------------------------------------------------
# (f) precision guards
# ---------------------------------------------------------------------------
def test_f_media_framing_is_a_span_not_a_whole_clause():
    framed = "I watched a movie where someone had a heart attack, what are the warning signs?"
    assert "heart attack" not in unsuppressed_text(framed)
    # The framing never swallows a live report that follows it.
    live = "I watched a movie where someone had a heart attack and since then my chest hurts"
    assert check_symptom_report(live) is not None
    b, _ = _both(live)
    assert b.verdict is Verdict.ESCALATE


@pytest.mark.parametrize(
    "question",
    [
        "I was watching a movie when I got crushing chest pain",
        "I saw a film last night and now I have chest pain",
        "while watching the news I had a seizure",
    ],
)
def test_f_media_activity_by_the_caller_is_not_framing(question):
    b, _ = _both(question)
    assert b.verdict is Verdict.ESCALATE, question


def test_f_media_framing_still_never_answers():
    # The danger-concept invariant sees through every context guard.
    q = BLIND1_FALSE_ESCALATIONS[0]
    assert mentions_danger_concept(q) is not None
    b, _ = _both(q)
    assert b.verdict is not Verdict.ANSWER


@pytest.mark.parametrize(
    "question",
    [
        "I get dizzy when I stand up but it goes away in a few seconds",
        "she had a fever yesterday but now she is fine",
        "I was a bit feverish last night, it's gone now",
    ],
)
def test_f_resolved_mild_symptoms_do_not_escalate(question):
    assert check_acute_concern(question) is None, question
    assert check_symptom_report(question) is None, question


@pytest.mark.parametrize(
    "question",
    [
        "I had chest pain this morning but it's gone now",
        "my son had a fit an hour ago, he's playing normally now",
        "I fainted earlier but it passed in a second",
        "my son had a fever but it's gone now, and now he is very drowsy and his neck is stiff",
        "I coughed up blood but it's fine now",
    ],
)
def test_f_resolution_never_suppresses_a_red_flag(question):
    b, _ = _both(question)
    assert b.verdict is Verdict.ESCALATE, question


@pytest.mark.parametrize(
    "question",
    [
        "Can I donate blood while on these medications?",
        "I want to give blood next week, is aspirin a problem?",
        "Is it safe to be a blood donor after surgery?",
    ],
)
def test_f_donating_blood_is_not_bleeding(question):
    assert check_symptom_report(question) is None, question
    assert check_red_flag(question) is None, question


def test_f_bleeding_still_fires_next_to_a_donation_word():
    assert check_symptom_report("I gave blood yesterday and the arm is bleeding through the plaster") is not None
