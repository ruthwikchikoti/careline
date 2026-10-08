"""Deterministic answer-text grounding (gate-chain final invariant, policy v7).

The v6 citation veto checks citation *ids* only. The final red team showed the
gap: a confident reasoner said "Take Metformin 1000mg twice daily." — the
SUPERSEDED dose — while citing the CURRENT fact's id (500mg), an affirming LLM
verifier agreed, and the Brain and the graph both ANSWERED. Nothing
deterministic tied the answer *text* to the cited fact.

This module is that tie. Every **claim-bearing token** of the answer text must
appear in the text of at least one CITED fact of the current valid slice:

* **quantities** — a number with a unit: dose / strength (``1000mg``,
  ``500 mg``, ``500 milligrams``, ``0.5 ml``), duration (``2 weeks``,
  ``5 days``), interval (``every 8 hours``) and count (``2 tablets``);
* **frequencies** — ``once daily`` / ``twice`` / ``thrice`` / ``3 times`` /
  ``three times`` / ``2x`` and the Latin shorthands (``od bd tds qid`` …);
* **bare numbers** — any other digit run (dates, ages, readings), except the
  emergency numbers 112 / 108 / 102 / 911 / 999;
* **drug names** — a curated lexicon, drug-class suffix families
  (``…cillin``, ``…floxacin``, ``…prazole``, ``…statin`` …) and the names of
  this patient's own medication facts (current AND retired, so a stopped drug
  is recognised as a drug even when no lexicon knows it).

Normalisation is deliberately small and symmetric (applied to the answer and
to the fact): case, spacing between number and unit, thousands commas,
trailing decimal zeros, unit spellings (``milligrams`` → ``mg``), number words
before a unit (``two weeks`` → ``2 week``) and frequency words (``twice`` →
``2 times``). Nothing is converted between units (``1 g`` is not ``1000 mg``)
and bare number words are ignored ("one of your medicines" is not a claim):
the check is conservative — an unmatched token never ANSWERS.

Pure, keyless, deterministic. Owner: Priyanshu (scope ``safety``).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable

from careline.domain.enums import FactKind
from careline.domain.model.fact import Fact
from careline.domain.model.patient import Patient

# -- vocabulary ---------------------------------------------------------------

#: Unit spellings -> canonical unit. A number followed by one of these is a
#: quantity claim and must match the SAME (number, unit) in a cited fact.
UNIT_CANONICAL: dict[str, str] = {
    **dict.fromkeys(("mg", "mgs", "milligram", "milligrams", "milligramme", "milligrammes"), "mg"),
    **dict.fromkeys(("mcg", "ug", "microgram", "micrograms"), "mcg"),
    **dict.fromkeys(("g", "gm", "gms", "gram", "grams", "gramme", "grammes"), "g"),
    **dict.fromkeys(("kg", "kgs", "kilogram", "kilograms"), "kg"),
    **dict.fromkeys(("ml", "mls", "millilitre", "millilitres", "milliliter", "milliliters", "cc"), "ml"),
    **dict.fromkeys(("l", "litre", "litres", "liter", "liters"), "l"),
    **dict.fromkeys(("iu", "unit", "units"), "unit"),
    **dict.fromkeys(("tablet", "tablets", "tab", "tabs", "pill", "pills", "capsule", "capsules",
                     "cap", "caps"), "tablet"),
    **dict.fromkeys(("drop", "drops"), "drop"),
    **dict.fromkeys(("puff", "puffs"), "puff"),
    **dict.fromkeys(("teaspoon", "teaspoons", "tsp", "tablespoon", "tablespoons", "tbsp",
                     "spoon", "spoons", "spoonful", "spoonfuls"), "spoon"),
    **dict.fromkeys(("time", "times", "x"), "times"),
    **dict.fromkeys(("sec", "secs", "seconds"), "second"),
    **dict.fromkeys(("min", "mins", "minute", "minutes"), "minute"),
    **dict.fromkeys(("h", "hr", "hrs", "hour", "hours"), "hour"),
    **dict.fromkeys(("day", "days"), "day"),
    **dict.fromkeys(("wk", "wks", "week", "weeks"), "week"),
    **dict.fromkeys(("month", "months"), "month"),
    **dict.fromkeys(("yr", "yrs", "year", "years"), "year"),
    **dict.fromkeys(("%", "percent", "per cent"), "percent"),
}

#: Number words, read only when a unit follows ("two weeks"), never bare.
NUMBER_WORDS: dict[str, str] = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "fourteen": "14", "fifteen": "15", "twenty": "20", "thirty": "30",
}

#: Frequency words / shorthands -> "N times".
FREQUENCY_WORDS: dict[str, str] = {
    "twice": "2", "thrice": "3",
    "od": "1", "qd": "1", "bd": "2", "bid": "2", "tds": "3", "tid": "3", "qid": "4", "qds": "4",
}
# "once" is a frequency only in a dosing frame ("once daily", "once at night"),
# never in "once the wound heals".
_ONCE_FRAME = frozenset({
    "daily", "day", "a", "per", "every", "each", "weekly", "week", "nightly", "night",
    "at", "in", "morning", "evening", "bedtime", "only",
})

#: Emergency numbers are instructions, not clinical claims.
EXEMPT_NUMBERS: frozenset[str] = frozenset({"112", "108", "102", "911", "999"})

#: Curated drug lexicon (generic names common in Indian / UK / US practice).
DRUG_LEXICON: frozenset[str] = frozenset({
    "paracetamol", "ibuprofen", "aspirin", "diclofenac", "naproxen", "tramadol", "morphine",
    "codeine", "oxycodone", "amoxicillin", "amoxiclav", "augmentin", "azithromycin",
    "ciprofloxacin", "doxycycline", "metronidazole", "cefixime", "ceftriaxone", "cephalexin",
    "cefuroxime", "penicillin", "clindamycin", "nitrofurantoin", "metformin", "glimepiride",
    "gliclazide", "insulin", "sitagliptin", "atorvastatin", "rosuvastatin", "simvastatin",
    "amlodipine", "telmisartan", "losartan", "lisinopril", "ramipril", "enalapril", "metoprolol",
    "atenolol", "propranolol", "bisoprolol", "furosemide", "hydrochlorothiazide",
    "spironolactone", "warfarin", "heparin", "enoxaparin", "clopidogrel", "apixaban",
    "rivaroxaban", "dabigatran", "omeprazole", "pantoprazole", "esomeprazole", "rabeprazole",
    "ranitidine", "famotidine", "ondansetron", "domperidone", "metoclopramide",
    "levothyroxine", "prednisolone", "prednisone", "dexamethasone", "hydrocortisone",
    "salbutamol", "montelukast", "budesonide", "cetirizine", "levocetirizine", "loratadine",
    "fexofenadine", "diphenhydramine", "chlorpheniramine", "sertraline", "fluoxetine",
    "escitalopram", "citalopram", "amitriptyline", "alprazolam", "lorazepam", "diazepam",
    "clonazepam", "zolpidem", "gabapentin", "pregabalin", "levetiracetam", "phenytoin",
    "carbamazepine", "valproate", "lithium", "haloperidol", "olanzapine", "quetiapine",
    "risperidone", "digoxin", "nitroglycerin", "isosorbide", "allopurinol", "colchicine",
    "methotrexate", "hydroxychloroquine", "ferrous", "crocin", "dolo", "calpol", "combiflam",
    "disprin", "ecosprin", "lactulose", "loperamide", "acyclovir", "oseltamivir",
    "fluconazole", "mupirocin", "chlorhexidine", "povidone",
})

#: Synonyms read as the same drug on both sides.
DRUG_SYNONYMS: dict[str, str] = {
    "acetaminophen": "paracetamol",
    "albuterol": "salbutamol",
    "thyroxine": "levothyroxine",
}

#: Drug-class suffix families (word >= 7 letters). Chosen so no everyday
#: English word ends with one ("…sterol" is deliberately absent).
DRUG_SUFFIXES: tuple[str, ...] = (
    "cillin", "mycin", "floxacin", "cycline", "prazole", "conazole", "nidazole", "tidine",
    "olol", "pril", "sartan", "dipine", "statin", "formin", "gliptin", "glitazone", "semide",
    "thiazide", "parin", "xaban", "gatran", "oxetine", "triptyline", "zepam", "zolam",
    "codone", "profen", "fenac", "coxib", "lukast", "triptan", "setron", "peridol",
    "gabalin", "pentin", "olone", "isone", "asone", "apine", "epine", "ovir", "avir",
)

#: Words inside a medication name that are forms, not the drug.
_FORM_WORDS = frozenset({
    "tablet", "tablets", "capsule", "capsules", "syrup", "injection", "cream", "ointment",
    "drops", "extended", "release", "oral", "solution", "suspension", "vitamin", "sodium",
    "hydrochloride", "with", "and", "plus", "forte", "gel", "spray", "inhaler", "patch",
})

_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[a-z]+|%")
_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+")


# -- normalisation -------------------------------------------------------------


def _norm_number(raw: str) -> list[str]:
    """'1,000' -> ['1000']; '0.50' -> ['0.5']; '2,5' -> ['2', '5'] (not a decimal)."""
    if _THOUSANDS.fullmatch(raw):
        raw = raw.replace(",", "")
    out = []
    for part in raw.split(","):
        if "." in part:
            whole, _, frac = part.partition(".")
            frac = frac.rstrip("0")
            part = f"{int(whole or '0')}.{frac}" if frac else str(int(whole or "0"))
        else:
            part = str(int(part))
        out.append(part)
    return out


def _canon_drug(word: str) -> str:
    word = DRUG_SYNONYMS.get(word, word)
    return word


def _is_drug(word: str, extra: frozenset[str]) -> str | None:
    """Canonical drug name if ``word`` names a drug, else None."""
    for cand in (word, word[:-1] if word.endswith("s") and len(word) > 4 else None):
        if not cand:
            continue
        canon = _canon_drug(cand)
        if canon in DRUG_LEXICON or canon in extra:
            return canon
        if len(cand) >= 7 and cand.endswith(DRUG_SUFFIXES):
            return canon
    return None


def _scan(text: str, extra_drugs: frozenset[str]) -> tuple[list[str], frozenset[str]]:
    """(claim tokens in order, every plain word) for ``text``."""
    words = _TOKEN.findall((text or "").lower().replace("µg", "mcg").replace("per cent", "percent"))
    tokens: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w[0].isdigit():
            nums = _norm_number(w)
            unit = UNIT_CANONICAL.get(nxt)
            for n in nums[:-1]:
                tokens.append(n)
            if unit is not None:
                tokens.append(f"{nums[-1]} {unit}")
                i += 2
                continue
            tokens.append(nums[-1])
        elif w in NUMBER_WORDS and UNIT_CANONICAL.get(nxt):
            # "two weeks", "three times" ("a day" / "an hour" are rates, not counts).
            tokens.append(f"{NUMBER_WORDS[w]} {UNIT_CANONICAL[nxt]}")
            i += 2
            continue
        elif w in FREQUENCY_WORDS:
            tokens.append(f"{FREQUENCY_WORDS[w]} times")
        elif w == "once" and nxt in _ONCE_FRAME:
            tokens.append("1 times")
        else:
            drug = _is_drug(w, extra_drugs)
            if drug is not None:
                tokens.append(f"drug:{drug}")
        i += 1
    plain = frozenset(_canon_drug(w) for w in words if w.isalpha())
    return tokens, plain


def answer_tokens(text: str, extra_drugs: Iterable[str] = ()) -> list[str]:
    """The claim-bearing tokens of ``text`` (ordered, de-duplicated)."""
    tokens, _ = _scan(text, frozenset(extra_drugs))
    seen: dict[str, None] = {}
    for t in tokens:
        if t not in EXEMPT_NUMBERS:
            seen.setdefault(t, None)
    return list(seen)


def _render(value: object) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return f"{value.day} {value:%B} {value:%b} {value.year} {value.isoformat()}"
    return str(value)


_NON_TEXT_FIELDS = frozenset({"id", "kind", "validity", "approved_by", "approved_at"})


def fact_text(fact: Fact) -> str:
    """Every doctor-authored text field of ``fact`` (summary + structured fields)."""
    parts = [fact.summary]
    for name, value in fact:
        if name in _NON_TEXT_FIELDS or name == "summary" or value is None:
            continue
        parts.append(_render(value))
    return " . ".join(parts)


def medication_names(facts: Iterable[Fact]) -> frozenset[str]:
    """Drug words named by medication facts (current or retired)."""
    out: set[str] = set()
    for f in facts:
        if f.kind is FactKind.MEDICATION:
            for w in re.findall(r"[a-z]+", str(getattr(f, "name", "")).lower()):
                if len(w) >= 4 and w not in _FORM_WORDS:
                    out.add(_canon_drug(w))
    return frozenset(out)


def _grounded_in(token: str, fact_tokens: set[str], fact_words: frozenset[str]) -> bool:
    if token.startswith("drug:"):
        name = token[5:]
        return token in fact_tokens or name in fact_words
    return token in fact_tokens or (" " not in token and any(
        t == token or t.split(" ")[0] == token for t in fact_tokens
    ))


def _pool(facts: Iterable[Fact], extra: frozenset[str]) -> tuple[set[str], frozenset[str]]:
    tokens: set[str] = set()
    words: set[str] = set()
    for f in facts:
        t, w = _scan(fact_text(f), extra)
        tokens.update(t)
        words.update(w)
    return tokens, frozenset(words)


def ungrounded_tokens(
    answer: str, cited: Iterable[Fact], extra_drugs: Iterable[str] = ()
) -> list[str]:
    """Tokens of ``answer`` that appear in no fact of ``cited`` (ordered).

    A quantity must match the same (number, unit); a bare number matches the
    number with or without a unit; a drug matches by canonical name.
    """
    cited = list(cited)
    extra = frozenset(extra_drugs) | medication_names(cited)
    fact_tokens, fact_words = _pool(cited, extra)
    return [
        t for t in answer_tokens(answer, extra)
        if not _grounded_in(t, fact_tokens, fact_words)
    ]


def facts_containing(token: str, facts: Iterable[Fact], extra_drugs: Iterable[str] = ()) -> list[str]:
    """Ids of ``facts`` whose text contains ``token`` (for the trace)."""
    extra = frozenset(extra_drugs)
    out = []
    for f in facts:
        t, w = _pool([f], extra)
        if _grounded_in(token, t, w):
            out.append(f.id)
    return out


def non_current_facts(patient: Patient, now: datetime) -> tuple[Fact, ...]:
    """This patient's facts that are NOT current at ``now`` — superseded, not
    yet valid, or unapproved. Never answer material; used only to recognise a
    retired drug name and to name the fact in the trace."""
    return tuple(f for f in patient.facts if not f.is_current(now))


__all__ = [
    "DRUG_LEXICON",
    "DRUG_SUFFIXES",
    "DRUG_SYNONYMS",
    "EXEMPT_NUMBERS",
    "FREQUENCY_WORDS",
    "NUMBER_WORDS",
    "UNIT_CANONICAL",
    "answer_tokens",
    "fact_text",
    "facts_containing",
    "medication_names",
    "non_current_facts",
    "ungrounded_tokens",
]
